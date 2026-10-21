"""Invoice entry points retain approved sources and reuse existing records safely."""
from decimal import Decimal
import re

from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import CostingAuditLog, CostingHeader, Invoice, Opportunity, ProductionOrder, QuickCosting
from crm.tests import test_quotation_invoice_form
from crm.views_invoice import _opportunity_invoice_source


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, FINANCIAL_CORE_REPORTING_ACTIVE=True)
class InvoiceSourceNavigationTests(TestCase):
    setUpTestData = classmethod(test_quotation_invoice_form.QuotationInvoiceFormTests.setUpTestData.__func__)
    actor = test_quotation_invoice_form.QuotationInvoiceFormTests.actor
    quick = test_quotation_invoice_form.QuotationInvoiceFormTests.quick

    def advanced(self, sales, opportunity=None):
        opportunity = opportunity or Opportunity.objects.create(customer=self.customer, assigned_to=sales)
        return CostingHeader.objects.create(
            opportunity=opportunity, customer=self.customer, style_name="Approved navigation quote",
            order_quantity=2, currency="CAD", factory_location="ca", manual_fob_per_piece=50, status="approved",
            quotation_number=f"QT-NAV-{CostingHeader.objects.count() + 1}", quoted_at=timezone.now(),
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo, quotation_approved_at=timezone.now(),
        )

    def source_url(self, source):
        key = "quick_quotation_id" if isinstance(source, QuickCosting) else "quotation_id"
        return f"{reverse('invoice_add')}?{key}={source.pk}"

    def existing_invoice(self, source):
        link = "quick_costing" if isinstance(source, QuickCosting) else "costing_header"
        return Invoice.objects.create(
            **{link: source}, opportunity=source.opportunity, customer=self.customer,
            invoice_number=f"INV-NAV-{Invoice.objects.count() + 1}", currency="CAD", invoice_region="CA",
            subtotal=100, total_amount=100,
        )

    def request_without_business_writes(self, method, url, data=None):
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url, data or {})
        writes = [entry["sql"] for entry in queries if re.match(
            r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+["`]?crm_', entry["sql"], re.IGNORECASE,
        )]
        self.assertEqual(writes, [], "Invoice navigation must not mutate business records")
        return response

    def assert_source_form(self, source, response, source_key, costing_label):
        self.assertEqual(response.status_code, 200)
        prefill = response.context["opportunity_prefill"]
        self.assertEqual(prefill[source_key], source.pk)
        self.assertEqual(prefill["source_opportunity_id"], source.opportunity_id)
        self.assertEqual(prefill["customer"], self.customer)
        self.assertEqual(prefill["quantity"], 2)
        self.assertEqual(prefill["unit_price_display"], "CAD $50.00")
        self.assertEqual(prefill["order_amount_display"], "CAD $100.00")
        self.assertEqual(response.context["form"].initial["subtotal"], Decimal("100"))
        self.assertContains(response, f'name="{source_key}" value="{source.pk}"')
        self.assertContains(response, f"<span>Costing reference</span><b>{costing_label}</b>", html=True)
        self.assertContains(response, source.quotation_number)

    def test_opportunity_uses_approved_quick_quote_and_retains_source_on_save(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        Opportunity.objects.filter(pk=quick.opportunity_id).update(
            order_currency="USD", order_value=9999, order_value_usd=9999,
        )
        order = ProductionOrder.objects.create(
            opportunity=quick.opportunity, source_quick_costing=quick,
            customer=self.customer, title="Existing approved production",
            order_code="NAV-QUICK-ORDER", qty_total=2,
        )
        self.client.force_login(sales)
        url = f"{reverse('invoice_add')}?opportunity_id={quick.opportunity_id}"
        response = self.request_without_business_writes("get", url)
        self.assert_source_form(quick, response, "source_quick_quotation_id", f"QC-{quick.pk}")
        saved = self.client.post(reverse("invoice_add"), {
            "source_opportunity_id": quick.opportunity_id, "source_quick_quotation_id": quick.pk,
            "invoice_number": "", "issue_date": "2026-08-01", "due_date": "2026-08-31",
            "deposit_percentage": "100", "notes": "Source navigation quick invoice",
            "subtotal": "1", "currency": "USD", "customer": "", "order": "",
        })
        self.assertEqual(saved.status_code, 302)
        invoice = Invoice.objects.get(quick_costing=quick)
        self.assertEqual(invoice.opportunity_id, quick.opportunity_id)
        self.assertEqual(invoice.customer_id, self.customer.pk)
        self.assertEqual(invoice.order_id, order.pk)
        self.assertIsNone(invoice.costing_header_id)
        self.assertEqual(invoice.total_amount, Decimal("100"))
        self.assertEqual(invoice.currency, "CAD")
        self.assertEqual(ProductionOrder.objects.count(), 1)

    def test_opportunity_uses_approved_advanced_quote_and_retains_source_on_save(self):
        sales = self.actor("Sales")
        quote = self.advanced(sales)
        Opportunity.objects.filter(pk=quote.opportunity_id).update(order_value=9999, order_value_usd=9999)
        self.client.force_login(sales)
        url = f"{reverse('invoice_add')}?opportunity_id={quote.opportunity_id}"
        response = self.request_without_business_writes("get", url)
        self.assert_source_form(quote, response, "source_quotation_id", f"COST-{quote.pk}")
        saved = self.client.post(reverse("invoice_add"), {
            "source_opportunity_id": quote.opportunity_id, "source_quotation_id": quote.pk,
            "invoice_number": "", "issue_date": "2026-08-01", "due_date": "2026-08-31",
            "deposit_percentage": "50", "notes": "Source navigation advanced invoice",
            "subtotal": "1", "currency": "USD", "customer": "",
        })
        self.assertEqual(saved.status_code, 302)
        invoice = Invoice.objects.get(costing_header=quote)
        self.assertEqual(invoice.opportunity_id, quote.opportunity_id)
        self.assertEqual(invoice.customer_id, self.customer.pk)
        self.assertIsNone(invoice.quick_costing_id)
        self.assertIsNone(invoice.order_id)
        self.assertEqual(invoice.total_amount, Decimal("100"))
        self.assertEqual(invoice.currency, "CAD")
        self.assertFalse(ProductionOrder.objects.exists())
        audit = CostingAuditLog.objects.get(costing=quote, action="invoice_created")
        self.assertEqual(audit.changed_by, sales)
        self.assertEqual(audit.note, invoice.invoice_number)
        repeated = self.request_without_business_writes("post", self.source_url(quote))
        self.assertRedirects(repeated, reverse("invoice_view", args=[invoice.pk]), fetch_redirect_response=False)
        self.assertEqual(CostingAuditLog.objects.filter(costing=quote, action="invoice_created").count(), 1)
        self.assertEqual(Invoice.objects.filter(costing_header=quote).count(), 1)

    def test_existing_quick_invoice_is_reused_without_fx_or_business_writes(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        existing = self.existing_invoice(quick)
        QuickCosting.objects.filter(pk=quick.pk).update(currency="BDT", exchange_rate_bdt_per_cad=None)
        self.client.force_login(sales)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self.request_without_business_writes(method, self.source_url(quick))
                self.assertRedirects(response, reverse("invoice_view", args=[existing.pk]), fetch_redirect_response=False)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_existing_advanced_invoice_is_reused_with_invalid_pricing(self):
        sales = self.actor("Sales")
        quote = self.advanced(sales)
        existing = self.existing_invoice(quote)
        CostingHeader.objects.filter(pk=quote.pk).update(order_quantity=0, manual_fob_per_piece=0)
        self.client.force_login(sales)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self.request_without_business_writes(method, self.source_url(quote))
                self.assertRedirects(response, reverse("invoice_view", args=[existing.pk]), fetch_redirect_response=False)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_opportunity_reuses_existing_invoice_on_get_and_post(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        existing = self.existing_invoice(quick)
        self.client.force_login(sales)
        url = f"{reverse('invoice_add')}?opportunity_id={quick.opportunity_id}"
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self.request_without_business_writes(method, url)
                self.assertRedirects(response, reverse("invoice_view", args=[existing.pk]), fetch_redirect_response=False)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_multiple_approved_quotes_require_explicit_source_without_writes(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        self.advanced(sales, quick.opportunity)
        self.client.force_login(sales)
        url = f"{reverse('invoice_add')}?opportunity_id={quick.opportunity_id}"
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self.request_without_business_writes(method, url)
                self.assertRedirects(response, reverse("opportunity_detail", args=[quick.opportunity_id]), fetch_redirect_response=False)
                self.assertIn("More than one approved quotation exists", " ".join(str(message) for message in get_messages(response.wsgi_request)))
        self.assertFalse(Invoice.objects.exists())

    def test_foreign_sales_and_wrong_country_finance_cannot_open_or_reuse_sources(self):
        sales = self.actor("Sales")
        unauthorized = (self.actor("Sales", "BD"), self.actor("Finance", "BD"))
        for source in (self.quick(sales), self.advanced(sales)):
            direct = self.source_url(source)
            opportunity_url = f"{reverse('invoice_add')}?opportunity_id={source.opportunity_id}"
            for actor in unauthorized:
                self.client.force_login(actor)
                for url in (direct, opportunity_url):
                    with self.subTest(source=type(source).__name__, actor=actor.username, url=url, existing=False):
                        self.assertEqual(self.request_without_business_writes("get", url).status_code, 403)
            self.existing_invoice(source)
            for actor in unauthorized:
                self.client.force_login(actor)
                for method in ("get", "post"):
                    for url, status in ((direct, 403), (opportunity_url, 404)):
                        with self.subTest(source=type(source).__name__, actor=actor.username, method=method, url=url, existing=True):
                            self.assertEqual(self.request_without_business_writes(method, url).status_code, status)
        self.assertEqual(Invoice.objects.count(), 2)

    def test_new_quick_invoice_with_missing_fx_is_still_blocked(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        QuickCosting.objects.filter(pk=quick.pk).update(currency="BDT", exchange_rate_bdt_per_cad=None)
        self.client.force_login(sales)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self.request_without_business_writes(method, self.source_url(quick))
                self.assertRedirects(response, reverse("quick_costing_client_quotation", args=[quick.pk]), fetch_redirect_response=False)
                self.assertIn("Currency conversion rate is required", " ".join(str(message) for message in get_messages(response.wsgi_request)))
        self.assertFalse(Invoice.objects.exists())

    def test_opportunity_cannot_invoice_an_older_approved_revision(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        QuickCosting.objects.create(
            opportunity=quick.opportunity, previous_revision=quick, revision_root=quick,
            revision_number=2, project_name="Pending revision", status=QuickCosting.STATUS_DRAFT,
        )
        self.client.force_login(sales)
        response = self.request_without_business_writes(
            "get", f"{reverse('invoice_add')}?opportunity_id={quick.opportunity_id}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Invoice.objects.exists())

    def test_opportunity_source_resolution_has_bounded_queries_without_n_plus_one(self):
        sales = self.actor("Sales")
        quick = self.quick(sales)
        with CaptureQueriesContext(connection) as single_queries:
            source = _opportunity_invoice_source(quick.opportunity_id)
        self.assertEqual(source, {"quick_quotation_id": quick.pk})
        self.assertLessEqual(len(single_queries), 8)
        for _ in range(6):
            self.advanced(sales, quick.opportunity)
        with CaptureQueriesContext(connection) as multiple_queries:
            source = _opportunity_invoice_source(quick.opportunity_id)
        self.assertIn("More than one approved quotation exists", source["error"])
        self.assertLessEqual(len(multiple_queries), len(single_queries))
        print(f"Invoice source resolver queries: one={len(single_queries)}, seven={len(multiple_queries)}; no N+1 growth")
