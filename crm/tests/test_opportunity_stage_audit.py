from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crm.models import AutomationNotification, Customer, Invoice, Opportunity, ProductionOrder, Shipment
from crm.services.opportunity_stage_audit import (
    build_opportunity_stage_audit,
    build_workflow_integrity_dashboard_metrics,
    render_crm_integrity_csv,
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
            details = Path(tmpdir) / "CRM_DATA_INTEGRITY_DETAILS.md"
            csv_output = Path(tmpdir) / "crm_integrity_export.csv"
            call_command(
                "audit_opportunity_stages",
                "--notify",
                "--output",
                str(output),
                "--details-output",
                str(details),
                "--csv-output",
                str(csv_output),
            )
            content = output.read_text(encoding="utf-8")
            details_content = details.read_text(encoding="utf-8")
            csv_content = csv_output.read_text(encoding="utf-8")

        self.assertIn("# Opportunity Stage Audit Report", content)
        self.assertIn("invoice_stage_incorrect", content)
        self.assertIn("# CRM Data Integrity Details", details_content)
        self.assertIn("repair_opportunity_stages --dry-run", details_content)
        self.assertIn("SAFE_AUTO_FIX", details_content)
        self.assertIn("opportunity_id", csv_content)
        self.assertTrue(
            AutomationNotification.objects.filter(
                source_key="opportunity-stage-audit:summary:ceo",
                is_resolved=False,
            ).exists()
        )

    def test_detail_records_and_csv_filters_include_repair_classifications(self):
        repairable = self._opportunity(stage="Proposal")
        self._invoice(repairable, total="500", paid="100")
        Opportunity.objects.filter(pk=repairable.pk).update(stage="Proposal")
        legacy_customer = Customer.objects.create(account_brand="Demo Test Customer")
        Opportunity.objects.create(
            customer=legacy_customer,
            stage="Proposal",
            product_type="Activewear",
            product_category="Hoodie",
        )

        audit = build_opportunity_stage_audit()
        records = audit["detail_records"]
        classes = {record["repair_classification"] for record in records}

        self.assertIn("SAFE_AUTO_FIX", classes)
        self.assertIn("IGNORE_LEGACY_TEST", classes)
        self.assertEqual(audit["metrics"]["legacy_test_records"], 1)
        self.assertIn("invoice_stage_incorrect", render_crm_integrity_csv(audit, filter_mode="broken"))
        self.assertIn("legacy_test_candidate", render_crm_integrity_csv(audit, filter_mode="legacy"))
        self.assertIn("SAFE_AUTO_FIX", render_crm_integrity_csv(audit, filter_mode="repairable"))

    def test_repair_commands_are_dry_run_only(self):
        opportunity = self._opportunity(stage="Proposal")
        invoice = self._invoice(opportunity, total="500", paid="100")
        Opportunity.objects.filter(pk=opportunity.pk).update(stage="Proposal")
        before = {
            "opportunity_stage": Opportunity.objects.get(pk=opportunity.pk).stage,
            "invoice_count": Invoice.objects.count(),
            "production_count": ProductionOrder.objects.count(),
            "shipment_count": Shipment.objects.count(),
        }

        for command in [
            "repair_opportunity_stages",
            "repair_invoice_links",
            "repair_production_links",
            "repair_shipment_completion",
        ]:
            out = StringIO()
            call_command(command, "--dry-run", stdout=out)
            self.assertIn("DRY RUN ONLY", out.getvalue())

        opportunity.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(opportunity.stage, before["opportunity_stage"])
        self.assertEqual(Invoice.objects.count(), before["invoice_count"])
        self.assertEqual(ProductionOrder.objects.count(), before["production_count"])
        self.assertEqual(Shipment.objects.count(), before["shipment_count"])
        self.assertEqual(invoice.paid_amount, Decimal("100"))
