"""Connected commercial/payment/production workflow on rollback-managed test data."""
from decimal import Decimal

from django.contrib.auth.models import Group
from django.db.models import Count, Sum
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMAuditLog, FinanceOperation, Invoice, InvoicePayment, JournalEntry, Lead, Opportunity, ProductionOrder, QuickCosting
from crm.services.costing_workflow import create_invoice_from_quick_costing
from crm.services.finance_operations import FinanceOperationError, review_operation
from crm.services.production_approval import APPROVAL_FIELD, production_readiness
from crm.tests import test_invoice_payment_live_fix


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, FINANCIAL_CORE_REPORTING_ACTIVE=True)
class FinalCRMIntegrationTests(TestCase):
    setUpTestData = classmethod(test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.setUpTestData.__func__)
    payment_data = test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.payment_data

    def actor(self, role, side):
        actor = type(self.ceo).objects.create_user(username=f"integration-{role}-{side}")
        actor.groups.add(Group.objects.get_or_create(name=role)[0])
        actor.access.role = side
        actor.access.can_accounting_ca = role == "Finance" and side == "CA"
        actor.access.can_accounting_bd = role == "Finance" and side == "BD"
        actor.access.save()
        return type(actor).objects.get(pk=actor.pk)

    def run_workflow(self, *, side, sample):
        sales, finance, operator = (self.actor(role, side) for role in ("Sales", "Finance", "Production"))
        currency = "BDT" if side == "BD" else "CAD"
        lead = Lead.objects.create(account_brand="Integration inquiry", assigned_to=sales, customer=self.customer, market=side)
        opportunity = Opportunity.objects.create(lead=lead, customer=self.customer, assigned_to=sales, order_currency=currency)
        quick = QuickCosting.objects.create(
            opportunity=opportunity, created_by=sales, salesperson=sales,
            project_name="Integration sample" if sample else "Integration bulk",
            pricing_type=QuickCosting.PRICING_SAMPLE if sample else QuickCosting.PRICING_FULL_PACKAGE,
            costing_purpose=QuickCosting.PURPOSE_SAMPLE if sample else QuickCosting.PURPOSE_BULK,
            quantity=10, currency=currency, selling_price_per_piece=100,
            status=QuickCosting.STATUS_SUBMITTED, approval_submitted_at=timezone.now(),
        )
        self.client.force_login(self.ceo)
        self.assertEqual(self.client.post(reverse("quick_costing_approve", args=[quick.pk])).status_code, 302)
        quick.refresh_from_db()
        self.assertEqual(quick.status, QuickCosting.STATUS_APPROVED)
        self.assertEqual(quick.approved_by_id, self.ceo.pk)
        self.assertEqual(self.client.post(reverse("quick_costing_convert_to_quotation", args=[quick.pk])).status_code, 302)
        quick.refresh_from_db()
        approval_snapshot = (quick.approved_at, quick.approved_by_id, quick.quotation_number, quick.quoted_at)
        self.client.force_login(sales)
        response = self.client.post(reverse("quick_costing_convert_to_invoice", args=[quick.pk]))
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(quick_costing=quick)
        self.assertEqual(invoice.customer_id, self.customer.pk)
        self.assertEqual(invoice.opportunity_id, opportunity.pk)
        self.assertEqual(invoice.invoice_region, side)
        self.assertEqual(invoice.currency, currency)
        duplicate, created = create_invoice_from_quick_costing(quick, user=sales)
        self.assertFalse(created)
        self.assertEqual(duplicate.pk, invoice.pk)
        invoice.deposit_percentage = Decimal("100") if sample else Decimal("37.5")
        invoice.save(update_fields=["deposit_percentage"])
        required = invoice.total_amount if sample else (invoice.total_amount * Decimal("0.375")).quantize(Decimal("0.01"))
        bank = self.bd_bank if side == "BD" else self.ca_bank

        for index, amount in enumerate((required / 2, required / 2)):
            payload = self.payment_data(invoice, amount, method="bank_transfer", account=bank, reference=f"INTEGRATION-{side}-{index}")
            response = self.client.post(reverse("invoice_payment_add", args=[invoice.pk]), payload)
            self.assertEqual(response.status_code, 302)
            operation = FinanceOperation.objects.filter(invoice=invoice).latest("pk")
            self.assertEqual(operation.state, FinanceOperation.STATE_PENDING)
            before = JournalEntry.objects.count()
            with self.assertRaises(FinanceOperationError):
                review_operation(operation, actor=sales, action="APPROVE", notes="Unauthorized")
            self.assertTrue(production_readiness(opportunity)["ready"])
            self.assertFalse(production_readiness(opportunity)["payment_verified"])
            self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
            verifier = finance if index == 0 else self.ceo
            posted = review_operation(operation, actor=verifier, action="APPROVE", notes="Bank receipt verified", confirm_self_approval=True)
            self.assertEqual(posted.state, FinanceOperation.STATE_POSTED)
            self.assertEqual(posted.approved_by_id, verifier.pk)
            self.assertEqual(posted.opportunity_id, opportunity.pk)
            self.assertEqual(JournalEntry.objects.count(), before + 1)
            with self.assertRaises(FinanceOperationError):
                review_operation(posted, actor=verifier, action="APPROVE", notes="Repeated")
            self.assertEqual(JournalEntry.objects.count(), before + 1)
            invoice.refresh_from_db()
        readiness = production_readiness(opportunity, user=operator)
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["can_release"])
        self.assertFalse(readiness["can_exception"])
        self.assertEqual(invoice.paid_amount, required)
        self.assertEqual(invoice.balance, invoice.total_amount - required)
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        self.client.force_login(operator)
        response = self.client.post(reverse("production_from_opportunity", args=[opportunity.pk]), {"action": "release"})
        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(opportunity=opportunity)
        self.assertEqual(order.production_order_type, "sampling" if sample else "bulk")
        self.assertEqual(order.created_by_id, operator.pk)
        self.client.post(reverse("production_from_opportunity", args=[opportunity.pk]), {"action": "release"})
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)
        quick.refresh_from_db()
        self.assertEqual(approval_snapshot, (quick.approved_at, quick.approved_by_id, quick.quotation_number, quick.quoted_at))
        self.assertFalse(CRMAuditLog.objects.filter(field_name=APPROVAL_FIELD).exists())
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 2)
        self.assertFalse(JournalEntry.objects.exclude(source_key="").values("source_key").annotate(n=Count("pk")).filter(n__gt=1).exists())
        for journal in JournalEntry.objects.all():
            for debit, credit in (("native_debit", "native_credit"), ("cad_debit", "cad_credit"), ("bdt_debit", "bdt_credit")):
                totals = journal.lines.aggregate(debit=Sum(debit), credit=Sum(credit))
                self.assertEqual(totals["debit"], totals["credit"])

    def test_canada_sampling_workflow(self):
        self.run_workflow(side="CA", sample=True)

    def test_canada_bulk_workflow(self):
        self.run_workflow(side="CA", sample=False)

    def test_bangladesh_sampling_workflow(self):
        self.run_workflow(side="BD", sample=True)

    def test_bangladesh_bulk_workflow(self):
        self.run_workflow(side="BD", sample=False)
