from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crm.models import AutomationNotification, Customer, Invoice, Opportunity, ProductionOrder, Shipment
from crm.services.opportunity_stage_audit import (
    build_opportunity_stage_audit,
    build_workflow_integrity_dashboard_metrics,
    sync_opportunity_stage_audit_notification,
)


class OpportunityStageAuditTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(account_brand="Audit Customer")

    def _opportunity(self, *, stage="Proposal", customer=True):
        return Opportunity.objects.create(
            customer=self.customer if customer else None,
            stage=stage,
            product_type="Activewear",
            product_category="Hoodie",
            order_currency="CAD",
            order_value_usd=Decimal("1000"),
        )

    def _invoice(self, opportunity, *, total="1000", paid="0", status="sent"):
        return Invoice.objects.create(
            invoice_number=f"INV-AUDIT-{opportunity.pk}-{Invoice.objects.count() + 1}",
            opportunity=opportunity,
            customer=opportunity.customer,
            issue_date=self.today,
            due_date=self.today,
            currency="CAD",
            total_amount=Decimal(total),
            paid_amount=Decimal(paid),
            status=status,
        )

    def _production_order(self, opportunity, title="Audit production"):
        return ProductionOrder.objects.create(
            opportunity=opportunity,
            customer=opportunity.customer,
            title=title,
            qty_total=100,
        )

    def test_audit_classifies_and_warns_without_repairing_data(self):
        clean = self._opportunity(stage="Proposal")
        invoice_wrong_stage = self._opportunity(stage="Proposal")
        self._invoice(invoice_wrong_stage, total="1000", paid="200")
        Opportunity.objects.filter(pk=invoice_wrong_stage.pk).update(stage="Proposal")

        production_wrong_stage = self._opportunity(stage="Negotiation")
        self._production_order(production_wrong_stage)

        completed_wrong_stage = self._opportunity(stage="Production")
        production = self._production_order(completed_wrong_stage)
        Shipment.objects.create(
            order=production,
            opportunity=completed_wrong_stage,
            customer=self.customer,
            ship_date=self.today,
            status="delivered",
        )

        missing_customer = self._opportunity(stage="Proposal", customer=False)

        duplicate_production = self._opportunity(stage="Production")
        self._production_order(duplicate_production, title="Audit production A")
        self._production_order(duplicate_production, title="Audit production B")

        audit = build_opportunity_stage_audit()
        codes = {warning["code"] for warning in audit["warnings"]}

        self.assertIn("invoice_stage_incorrect", codes)
        self.assertIn("production_stage_incorrect", codes)
        self.assertIn("completed_stage_incorrect", codes)
        self.assertIn("missing_customer", codes)
        self.assertIn("duplicate_production_links", codes)
        self.assertGreaterEqual(audit["metrics"]["workflow_errors"], 5)
        self.assertGreaterEqual(audit["metrics"]["broken_opportunities"], 4)

        invoice_wrong_stage.refresh_from_db()
        production_wrong_stage.refresh_from_db()
        missing_customer.refresh_from_db()
        self.assertEqual(invoice_wrong_stage.stage, "Proposal")
        self.assertEqual(production_wrong_stage.stage, "Negotiation")
        self.assertIsNone(missing_customer.customer_id)
        self.assertEqual(clean.stage, "Proposal")

    def test_dashboard_metrics_use_same_audit_rules(self):
        opportunity = self._opportunity(stage="Proposal")
        self._invoice(opportunity, total="500", paid="100")
        Opportunity.objects.filter(pk=opportunity.pk).update(stage="Proposal")

        metrics = build_workflow_integrity_dashboard_metrics()

        self.assertGreaterEqual(metrics["workflow_errors"], 1)
        self.assertGreaterEqual(metrics["broken_opportunities"], 1)
        self.assertEqual(metrics["awaiting_payment_count"], 0)

    def test_notification_summary_is_created_and_resolved(self):
        opportunity = self._opportunity(stage="Proposal")
        self._invoice(opportunity, total="500", paid="100")
        Opportunity.objects.filter(pk=opportunity.pk).update(stage="Proposal")

        audit = build_opportunity_stage_audit()
        result = sync_opportunity_stage_audit_notification(audit)

        self.assertTrue(result["active"])
        notification = AutomationNotification.objects.get(source_key=result["source_key"])
        self.assertEqual(notification.assigned_role, "CEO")
        self.assertFalse(notification.is_resolved)
        self.assertIn("workflow warning", notification.message)

        Invoice.objects.all().delete()
        Opportunity.objects.filter(pk=opportunity.pk).update(stage="Proposal")
        clean_audit = build_opportunity_stage_audit()
        sync_opportunity_stage_audit_notification(clean_audit)
        notification.refresh_from_db()
        self.assertTrue(notification.is_resolved)

    def test_management_command_writes_report_and_notification(self):
        opportunity = self._opportunity(stage="Proposal")
        self._invoice(opportunity, total="500", paid="100")
        Opportunity.objects.filter(pk=opportunity.pk).update(stage="Proposal")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "OPPORTUNITY_STAGE_AUDIT_REPORT.md"
            call_command("audit_opportunity_stages", "--notify", "--output", str(output))
            content = output.read_text(encoding="utf-8")

        self.assertIn("# Opportunity Stage Audit Report", content)
        self.assertIn("invoice_stage_incorrect", content)
        self.assertTrue(
            AutomationNotification.objects.filter(
                source_key="opportunity-stage-audit:summary:ceo",
                is_resolved=False,
            ).exists()
        )
