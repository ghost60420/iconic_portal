from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from crm.models import Customer, CostingHeader, Invoice, InvoicePayment, Lead, Opportunity, OrderLifecycle, ProductionOrder, QuickCosting, Shipment
from crm.services.workflow_visibility import _invoice_workflow_relationships, build_workflow_visibility_context
from crm.views_invoice import invoice_view


class InvoiceDetailPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("invoice-perf", "invoice-perf@example.com", "test-pass")
        cls.customer = Customer.objects.create(account_brand="Invoice performance")
        cls.lead = Lead.objects.create(customer=cls.customer, account_brand="Invoice performance", assigned_to=cls.user)
        cls.opportunity = Opportunity.objects.create(customer=cls.customer, lead=cls.lead, assigned_to=cls.user)
        cls.quick = QuickCosting.objects.create(
            opportunity=cls.opportunity, buyer_name="Buyer", project_name="Invoice performance",
            currency="CAD", quantity=10, selling_price_per_piece=Decimal("50"), created_by=cls.user,
        )
        cls.invoice = Invoice.objects.create(
            customer=cls.customer, quick_costing=cls.quick, invoice_number="INV-PERF",
            issue_date=date(2026, 9, 30), currency="CAD", subtotal=Decimal("500"), total_amount=Decimal("500"),
        )
        cls.lifecycle = OrderLifecycle.objects.create(invoice=cls.invoice, lead=cls.lead, opportunity=cls.opportunity)

    def render_detail(self):
        request = RequestFactory().get(f"/invoices/{self.invoice.pk}/")
        request.user = get_user_model().objects.get(pk=self.user.pk)
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        with CaptureQueriesContext(connection) as queries:
            response = invoice_view(request, self.invoice.pk)
        self.assertEqual(response.status_code, 200)
        return response, len(queries)

    def test_invoice_detail_warm_budget_and_payment_rows_do_not_add_queries(self):
        # Prime shared navigation caches, which are outside invoice data loading.
        self.render_detail()
        _, before = self.render_detail()
        self.assertLessEqual(before, 8)
        InvoicePayment.objects.bulk_create([
            InvoicePayment(invoice=self.invoice, payment_date=date(2026, 9, 30), amount=Decimal("1"), created_by=self.user)
            for _ in range(20)
        ])
        response, after = self.render_detail()
        self.assertEqual(after, before)
        self.assertContains(response, "Invoice performance")

    def test_workflow_relationship_counts_are_independent_and_bounded(self):
        CostingHeader.objects.bulk_create([CostingHeader(opportunity=self.opportunity) for _ in range(12)])
        QuickCosting.objects.bulk_create([
            QuickCosting(opportunity=self.opportunity, buyer_name="Buyer", project_name=f"Costing {index}")
            for index in range(15)
        ])
        ProductionOrder.objects.bulk_create([
            ProductionOrder(opportunity=self.opportunity, title=f"Order {index}", order_code=f"PERF-{index}")
            for index in range(8)
        ])
        Shipment.objects.bulk_create([Shipment(opportunity=self.opportunity) for _ in range(9)])
        with self.assertNumQueries(1):
            counts = _invoice_workflow_relationships(self.opportunity)
        self.assertEqual(counts["advanced_count"], 12)
        self.assertEqual(counts["quick_count"], 16)
        self.assertTrue(counts["has_production"])
        self.assertTrue(counts["has_shipment"])

    def test_workflow_navigation_summary_and_timeline_match_existing_resolution(self):
        CostingHeader.objects.create(opportunity=self.opportunity, style_name="Earlier costing")
        CostingHeader.objects.create(opportunity=self.opportunity, style_name="Latest costing")
        order = ProductionOrder.objects.create(opportunity=self.opportunity, title="Production", order_code="PERF-LINK")
        shipment = Shipment.objects.create(opportunity=self.opportunity, order=order, ship_date=date(2026, 10, 1))
        Shipment.objects.create(opportunity=self.opportunity, ship_date=date(2026, 10, 2))

        def context():
            invoice = Invoice.objects.get(pk=self.invoice.pk)
            return build_workflow_visibility_context("invoice", user=self.user, invoice=invoice)

        optimized = context()
        with patch("crm.services.workflow_visibility._invoice_workflow_relationships", return_value=None):
            original = context()
        self.assertEqual(optimized, original)
        shipping = next(item for item in optimized["workflow_nav_items"] if item["key"] == "shipping")
        self.assertIn(f"/{shipment.pk}/", shipping["url"])
