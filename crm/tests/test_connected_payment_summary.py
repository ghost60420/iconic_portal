"""The same verified receipts appear in Sales and Production without release gating."""
from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import FinanceOperation, Invoice, InvoicePayment, Opportunity, ProductionOrder, QuickCosting
from crm.services.finance_operations import review_operation
from crm.tests import test_invoice_payment_live_fix


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, FINANCIAL_CORE_REPORTING_ACTIVE=True)
class ConnectedPaymentSummaryTests(TestCase):
    payment_data = test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.payment_data

    @classmethod
    def setUpTestData(cls):
        test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.setUpTestData.__func__(cls)
        cls.employee.groups.add(Group.objects.get_or_create(name="Sales")[0])
        cls.employee.access.role = "CA"
        cls.employee.access.save(update_fields=["role"])
        cls.opportunity = Opportunity.objects.create(customer=cls.customer, assigned_to=cls.employee)
        cls.quick = QuickCosting.objects.create(
            opportunity=cls.opportunity, salesperson=cls.employee, created_by=cls.employee,
            project_name="Connected summary sample", quantity=1, currency="CAD", selling_price_per_piece=100,
            costing_purpose=QuickCosting.PURPOSE_SAMPLE, pricing_type=QuickCosting.PRICING_SAMPLE,
            status=QuickCosting.STATUS_INVOICED, approved_by=cls.ceo, approved_at=timezone.now(),
            quotation_number="QQT-SUMMARY", quoted_by=cls.ceo, quoted_at=timezone.now(),
        )
        cls.order = ProductionOrder.objects.create(
            opportunity=cls.opportunity, customer=cls.customer, title="Existing production",
        )
        cls.invoice = Invoice.objects.create(
            opportunity=cls.opportunity, quick_costing=cls.quick, customer=cls.customer, order=cls.order,
            invoice_number="INV-SUMMARY", currency="CAD", invoice_region="CA", invoice_type="sample",
            subtotal=100, total_amount=100, paid_amount=0,
        )

    def record(self, amount, reference):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("invoice_payment_add", args=[self.invoice.pk]), self.payment_data(
            self.invoice, amount, method="bank_transfer", account=self.ca_bank, reference=reference,
        ))
        self.assertEqual(response.status_code, 302, response.context["payment_form"].errors if response.status_code == 200 else "")
        return FinanceOperation.objects.filter(invoice=self.invoice).latest("pk")

    def summaries(self):
        self.client.force_login(self.ceo)
        opportunity = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        production = self.client.get(reverse("production_detail", args=[self.order.pk]))
        self.assertEqual(opportunity.status_code, 200)
        self.assertEqual(production.status_code, 200)
        return opportunity.context["production_readiness"], production.context["production_payment_rows"][0]

    def test_partial_full_and_rejected_receipts_share_one_summary(self):
        pending = self.record("40", "SUMMARY-PARTIAL")
        readiness, row = self.summaries()
        self.assertEqual(readiness["payment_status"], "PENDING VERIFICATION")
        self.assertEqual(row["summary"]["payment_status"], "PENDING VERIFICATION")
        self.assertEqual(row["summary"]["remaining_balance"], Decimal("100"))
        review_operation(pending, actor=self.ceo, action="APPROVE", notes="Synthetic receipt checked")
        readiness, row = self.summaries()
        self.assertEqual(readiness["amount_paid"], Decimal("40"))
        self.assertEqual(readiness["outstanding_balance"], Decimal("60"))
        self.assertEqual(row["summary"]["payment_status"], "PARTIALLY PAID")
        self.assertEqual(row["summary"]["remaining_balance"], Decimal("60"))
        rejected = self.record("20", "SUMMARY-REJECTED")
        review_operation(rejected, actor=self.ceo, action="REJECT", notes="Synthetic receipt not received")
        self.assertEqual(self.summaries()[1]["summary"]["remaining_balance"], Decimal("60"))
        balance = self.record("60", "SUMMARY-FINAL")
        review_operation(balance, actor=self.ceo, action="APPROVE", notes="Synthetic balance checked")
        readiness, row = self.summaries()
        self.assertEqual(readiness["outstanding_balance"], Decimal("0"))
        self.assertEqual(row["summary"]["payment_status"], "FULLY PAID")
        self.assertEqual(InvoicePayment.objects.filter(invoice=self.invoice).count(), 2)
        self.assertEqual(ProductionOrder.objects.count(), 1)

    def test_production_only_role_cannot_see_new_financial_summary(self):
        operator = type(self.ceo).objects.create_user(username="summary-production")
        operator.groups.add(Group.objects.get_or_create(name="Production")[0])
        self.order.assigned_production_manager = operator
        self.order.save(update_fields=["assigned_production_manager"])
        self.client.force_login(operator)
        response = self.client.get(reverse("production_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["production_payment_rows"], [])
        self.assertNotContains(response, 'id="production-invoice-payment-summary"')

    def test_summary_permissions_and_pending_states_do_not_query_per_invoice(self):
        self.client.force_login(self.ceo)
        url = reverse("production_detail", args=[self.order.pk])
        self.client.get(url)
        with CaptureQueriesContext(connection) as before:
            self.client.get(url)
        for index in range(5):
            Invoice.objects.create(
                order=self.order, opportunity=self.opportunity, customer=self.customer,
                invoice_number=f"INV-SUMMARY-EXTRA-{index}", currency="CAD", invoice_region="CA", total_amount=10,
            )
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(url)
        self.assertEqual(len(response.context["production_payment_rows"]), 6)
        self.assertEqual(len(after), len(before))

    def test_existing_payment_page_link_uses_the_authorized_invoice(self):
        self.client.force_login(self.ceo)
        for name, pk in (("opportunity_detail", self.opportunity.pk), ("production_detail", self.order.pk)):
            response = self.client.get(reverse(name, args=[pk]))
            self.assertContains(response, reverse("invoice_payment_add", args=[self.invoice.pk]))
