from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    CostingHeader, CostSheet, Customer, Event, FavoriteRecord, Invoice, Lead,
    LeadComment, Opportunity, OpportunityDocument, OpportunityFile, OrderLifecycle,
    OpportunityTask, ProductionOrder, QuickCosting, Shipment,
)
from crm.views import _opportunity_linked_record_labels


class OpportunityDetailPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("opp-perf", "opp-perf@example.test", "test")
        cls.other = get_user_model().objects.create_superuser("opp-perf-other", "other@example.test", "test")
        cls.customer = Customer.objects.create(account_brand="Opportunity performance")
        cls.lead = Lead.objects.create(customer=cls.customer, account_brand="Opportunity performance", assigned_to=cls.user)
        cls.opportunity = Opportunity.objects.create(customer=cls.customer, lead=cls.lead, assigned_to=cls.user)
        cls.quick = QuickCosting.objects.create(
            opportunity=cls.opportunity, project_name="Sample performance", quantity=7,
            costing_purpose=QuickCosting.PURPOSE_SAMPLE, currency="CAD",
            selling_price_per_piece=Decimal("50"), created_by=cls.user,
            status=QuickCosting.STATUS_INVOICED, approved_by=cls.user, approved_at=timezone.now(),
            quotation_number="QQT-PERF", quoted_by=cls.user, quoted_at=timezone.now(),
        )
        cls.invoice = Invoice.objects.create(
            customer=cls.customer, quick_costing=cls.quick, invoice_number="INV-OPP-PERF",
            currency="CAD", subtotal=Decimal("350"), total_amount=Decimal("350"),
            paid_amount=Decimal("0"), invoice_type="sample",
        )

    def detail(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertEqual(response.status_code, 200)
        return response, list(queries)

    def test_full_request_query_bound_does_not_grow_with_costings_invoices_or_tasks(self):
        self.client.force_login(self.user)
        self.detail()  # Prime existing global navigation caches and recent history.
        _, before = self.detail()
        # Without an OrderLifecycle, the existing workflow navigator performs
        # two bounded fallback reads to discover the quick costing and invoice.
        self.assertLessEqual(len(before), 24)
        for index in range(5):
            quick = QuickCosting.objects.create(
                opportunity=self.opportunity, project_name=f"Additional costing {index}",
                created_by=self.user, quantity=2,
            )
            Invoice.objects.create(
                customer=self.customer, quick_costing=quick,
                invoice_number=f"INV-OPP-EXTRA-{index}", total_amount=Decimal("10"),
            )
            OpportunityTask.objects.create(opportunity=self.opportunity, title=f"Task {index}")
        response, after = self.detail()
        self.assertEqual(len(after), len(before))
        self.assertEqual(len(response.context["quick_costing_rows"]), 6)
        self.assertContains(response, "Task 4")
        self.assertFalse(response.context["production_readiness"]["ready"])
        self.assertNotContains(response, "READY FOR PRODUCTION")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0"))

    def test_existing_lifecycle_uses_the_reduced_full_request_query_bound(self):
        OrderLifecycle.objects.create(
            customer=self.customer, lead=self.lead, opportunity=self.opportunity,
            invoice=self.invoice, status="invoice",
        )
        self.client.force_login(self.user)
        self.detail()
        response, queries = self.detail()
        self.assertLessEqual(len(queries), 22)
        self.assertContains(response, self.invoice.invoice_number)
        self.assertTrue(response.context["production_readiness"]["ready"])
        self.assertFalse(response.context["production_readiness"]["payment_verified"])

    def test_favorite_is_current_user_scoped_and_refreshed_each_request(self):
        self.client.force_login(self.user)
        response, _ = self.detail()
        self.assertFalse(response.context["crm_current_record"]["is_favorite"])
        favorite = FavoriteRecord.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(Opportunity),
            object_id=self.opportunity.pk, record_type="Opportunity", record_label="Favorite",
            target_url=reverse("opportunity_detail", args=[self.opportunity.pk]),
        )
        response, queries = self.detail()
        self.assertTrue(response.context["crm_current_record"]["is_favorite"])
        self.assertFalse(any(query["sql"].startswith('SELECT 1 AS "a" FROM "crm_favoriterecord"') for query in queries))
        self.client.force_login(self.other)
        response, _ = self.detail()
        self.assertFalse(response.context["crm_current_record"]["is_favorite"])
        favorite.delete()
        self.client.force_login(self.user)
        response, _ = self.detail()
        self.assertFalse(response.context["crm_current_record"]["is_favorite"])

    def test_empty_and_legacy_cost_sheet_deletion_guards_remain_bounded(self):
        empty = Opportunity.objects.create(customer=self.customer, lead=self.lead)
        with self.assertNumQueries(1):
            self.assertEqual(_opportunity_linked_record_labels(empty), [])
        # Inactive legacy cost sheets must still prevent deletion.
        CostSheet.objects.create(opportunity=empty, is_active=False)
        with self.assertNumQueries(1):
            self.assertEqual(_opportunity_linked_record_labels(empty), ["costings"])

    def test_linked_record_labels_preserve_every_existing_guard_and_label_order(self):
        ProductionOrder.objects.create(opportunity=self.opportunity, title="Guard order", order_code="OPP-PERF-GUARD")
        CostingHeader.objects.create(opportunity=self.opportunity)
        CostSheet.objects.create(opportunity=self.opportunity)
        Shipment.objects.create(opportunity=self.opportunity)
        OpportunityTask.objects.create(opportunity=self.opportunity, title="Guard task")
        OpportunityFile.objects.create(opportunity=self.opportunity, original_name="Guard file")
        OpportunityDocument.objects.create(opportunity=self.opportunity, original_name="Guard document")
        LeadComment.objects.create(opportunity=self.opportunity, lead=self.lead, content="Guard comment")
        Event.objects.create(opportunity=self.opportunity, title="Guard event", start_datetime=timezone.now())
        with self.assertNumQueries(1):
            labels = _opportunity_linked_record_labels(self.opportunity)
        self.assertEqual(labels, ["production orders", "invoices", "costings", "shipments", "tasks", "files", "comments", "calendar events"])
