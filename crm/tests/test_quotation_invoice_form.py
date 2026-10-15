"""Approved quotations use the existing invoice and verified receipt workflow."""
from decimal import Decimal

from django.contrib.auth.models import Group
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import CostingHeader, FinanceOperation, Invoice, InvoicePayment, Opportunity, ProductionOrder, QuickCosting
from crm.services.finance_operations import review_operation
from crm.tests import test_invoice_payment_live_fix


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, FINANCIAL_CORE_REPORTING_ACTIVE=True)
class QuotationInvoiceFormTests(TestCase):
    setUpTestData = classmethod(test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.setUpTestData.__func__)
    payment_data = test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.payment_data

    def actor(self, role, side="CA"):
        actor = type(self.ceo).objects.create_user(username=f"quote-{role}-{side}")
        actor.groups.add(Group.objects.get_or_create(name=role)[0])
        actor.access.role = side
        actor.access.can_accounting_ca = role == "Finance" and side == "CA"
        actor.access.can_accounting_bd = role == "Finance" and side == "BD"
        actor.access.save()
        return type(actor).objects.get(pk=actor.pk)

    def quick(self, sales, side="CA"):
        self.customer.country = "Bangladesh" if side == "BD" else "Canada"
        self.customer.save(update_fields=["country"])
        opportunity = Opportunity.objects.create(customer=self.customer, assigned_to=sales)
        return QuickCosting.objects.create(
            opportunity=opportunity, created_by=sales, salesperson=sales,
            project_name="Approved quotation sample", quantity=2, currency="BDT" if side == "BD" else "CAD",
            selling_price_per_piece=50, costing_purpose=QuickCosting.PURPOSE_SAMPLE,
            pricing_type=QuickCosting.PRICING_SAMPLE, status=QuickCosting.STATUS_QUOTED,
            approved_by=self.ceo, approved_at=timezone.now(),
            quotation_number=f"QQT-FORM-{side}", quoted_by=self.ceo, quoted_at=timezone.now(),
        )

    def save_invoice(self, quick, actor):
        self.client.force_login(actor)
        url = f"{reverse('invoice_add')}?quick_quotation_id={quick.pk}"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["opportunity_prefill"]["quantity"], 2)
        self.assertEqual(page.context["form"].initial["subtotal"], Decimal("100"))
        response = self.client.post(url, {
            "source_quick_quotation_id": quick.pk, "invoice_number": "", "issue_date": "2026-08-01",
            "due_date": "2026-08-31", "deposit_percentage": "100", "notes": "Synthetic form invoice",
            # Protected approved source fields cannot be changed by a crafted Sales POST.
            "subtotal": "1", "total_amount": "1", "currency": "USD", "customer": "",
            "shipping_amount": "900", "discount_amount": "99", "tax_amount": "99",
        })
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.status_code == 200 else "")
        invoice = Invoice.objects.get(quick_costing=quick)
        self.assertEqual(invoice.total_amount, Decimal("100"))
        self.assertEqual(invoice.subtotal, Decimal("100"))
        self.assertEqual(invoice.customer_id, self.customer.pk)
        self.assertEqual(invoice.opportunity_id, quick.opportunity_id)
        self.assertEqual(invoice.currency, quick.currency)
        self.assertEqual(invoice.paid_amount, Decimal("0"))
        return invoice, url

    def run_connected_form_workflow(self, side):
        sales, finance, production = [self.actor(role, side) for role in ("Sales", "Finance", "Production")]
        quick = self.quick(sales, side)
        approved_at = quick.approved_at
        self.client.force_login(sales)
        quotation = self.client.get(reverse("quick_costing_client_quotation", args=[quick.pk]))
        self.assertContains(quotation, f"{reverse('invoice_add')}?quick_quotation_id={quick.pk}")
        invoice, url = self.save_invoice(quick, sales)
        self.assertFalse(ProductionOrder.objects.exists())
        self.assertRedirects(self.client.get(url), reverse("invoice_view", args=[invoice.pk]), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(url, {}), reverse("invoice_view", args=[invoice.pk]), fetch_redirect_response=False)
        self.assertEqual(Invoice.objects.filter(quick_costing=quick).count(), 1)
        self.client.force_login(production)
        release = reverse("production_from_opportunity", args=[quick.opportunity_id])
        self.assertEqual(self.client.post(release, {"action": "release"}).status_code, 302)
        order = ProductionOrder.objects.get(opportunity=quick.opportunity)
        self.client.post(release, {"action": "release"})
        self.assertEqual(ProductionOrder.objects.filter(opportunity=quick.opportunity).count(), 1)
        for index, amount in enumerate(("40", "60")):
            self.client.force_login(sales)
            payload = self.payment_data(invoice, amount, account=self.bd_bank if side == "BD" else self.ca_bank,
                                        method="bank_transfer", reference=f"FORM-{side}-{index}")
            response = self.client.post(reverse("invoice_payment_add", args=[invoice.pk]), payload)
            self.assertEqual(response.status_code, 302)
            operation = FinanceOperation.objects.filter(invoice=invoice).latest("pk")
            self.assertEqual(operation.state, FinanceOperation.STATE_PENDING)
            verifier = finance if index == 0 else self.ceo
            posted = review_operation(operation, actor=verifier, action="APPROVE", notes="Synthetic bank receipt verified")
            self.assertEqual(posted.state, FinanceOperation.STATE_POSTED)
            totals = posted.primary_journal.lines.aggregate(debit=Sum("native_debit"), credit=Sum("native_credit"))
            self.assertEqual(totals["debit"], totals["credit"])
            invoice.refresh_from_db()
            self.client.force_login(self.ceo)
            detail = self.client.get(reverse("production_detail", args=[order.pk]))
            self.assertEqual(detail.context["production_payment_rows"][0]["summary"]["remaining_balance"], invoice.balance)
        self.assertEqual(invoice.balance, 0)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 2)
        quick.refresh_from_db()
        self.assertEqual(quick.approved_at, approved_at)
        self.assertEqual(ProductionOrder.objects.count(), 1)

    def test_canada_quotation_form_to_verified_payments_and_unpaid_release(self):
        self.run_connected_form_workflow("CA")

    def test_authorized_historical_reporting_date_is_retained_on_new_invoice(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        self.client.force_login(self.ceo)
        response = self.client.post(f"{reverse('invoice_add')}?quick_quotation_id={quick.pk}", {
            "source_quick_quotation_id": quick.pk, "issue_date": "2026-08-01", "due_date": "2026-08-31",
            "deposit_percentage": "100", "shipping_amount": "0", "discount_amount": "0", "tax_amount": "0",
            "historical_entry_mode": "on", "invoice_date": "2026-07-31",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(str(Invoice.objects.get(quick_costing=quick).invoice_date), "2026-07-31")

    def test_bangladesh_quotation_form_to_verified_payments_and_unpaid_release(self):
        self.run_connected_form_workflow("BD")

    def test_unapproved_quote_and_another_salesperson_are_denied(self):
        owner, stranger = self.actor("Sales"), self.actor("Sales", "BD")
        quick = self.quick(owner)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(f"{reverse('invoice_add')}?quick_quotation_id={quick.pk}").status_code, 403)
        self.client.force_login(owner)
        quick.approved_at = None
        quick.save(update_fields=["approved_at"])
        self.assertEqual(self.client.get(f"{reverse('invoice_add')}?quick_quotation_id={quick.pk}").status_code, 403)
        self.assertFalse(Invoice.objects.exists())

    def test_invoice_view_preserves_sales_ownership_and_finance_country_scope(self):
        owner = self.actor("Sales")
        invoice, _url = self.save_invoice(self.quick(owner), owner)
        self.assertEqual(self.client.get(reverse("invoice_view", args=[invoice.pk])).status_code, 200)
        for actor in (self.actor("Sales", "BD"), self.actor("Finance", "BD")):
            self.client.force_login(actor)
            self.assertEqual(self.client.get(reverse("invoice_view", args=[invoice.pk])).status_code, 404)

    def test_approved_sent_advanced_quote_and_invoice_remain_accessible(self):
        sales = self.actor("Sales")
        opportunity = Opportunity.objects.create(customer=self.customer, assigned_to=sales)
        quotation = CostingHeader.objects.create(
            opportunity=opportunity, customer=self.customer, style_name="Approved advanced sample",
            order_quantity=2, currency="CAD", manual_fob_per_piece=50, status="approved",
            quotation_number="QT-SENT", quoted_at=timezone.now(),
            quotation_status=CostingHeader.QUOTATION_STATUS_SENT,
            quotation_approved_by=self.ceo, quotation_approved_at=timezone.now(),
        )
        invoice = Invoice.objects.create(
            costing_header=quotation, opportunity=opportunity, customer=self.customer,
            invoice_number="INV-SENT", currency="CAD", subtotal=100, total_amount=100,
        )
        self.client.force_login(sales)
        self.assertRedirects(self.client.get(f"{reverse('invoice_add')}?quotation_id={quotation.pk}"),
                             reverse("invoice_view", args=[invoice.pk]), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("invoice_view", args=[invoice.pk])).status_code, 200)
