"""Production assignment is visible metadata, not a financial or release control."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.forms import OpportunityProductionTypeForm
from crm.models import (
    AccountingEntry, CRMAuditLog, Customer, FinanceOperation, FinancialAuditEvent,
    Invoice, InvoiceFinancialState, InvoicePayment, JournalEntry, JournalLine,
    Opportunity, ProductionOrder, QuickCosting,
)
from crm.services.production_approval import production_readiness


class OpportunityProductionTypeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.ceo = users.objects.create_superuser(username="type-ceo", password="test")
        cls.sales = users.objects.create_user(username="type-sales")
        cls.other_sales = users.objects.create_user(username="type-other-sales")
        cls.finance = users.objects.create_user(username="type-finance")
        cls.production = users.objects.create_user(username="type-production")
        for user, role in (
            (cls.sales, "Sales"), (cls.other_sales, "Sales"),
            (cls.finance, "Finance"), (cls.production, "Production"),
        ):
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        cls.customer = Customer.objects.create(account_brand="Assignment test customer")
        cls.opportunity = Opportunity.objects.create(
            customer=cls.customer, assigned_to=cls.sales, moq_units=2, order_currency="CAD",
        )
        cls.quick = QuickCosting.objects.create(
            opportunity=cls.opportunity, project_name="Assignment test sample",
            costing_purpose=QuickCosting.PURPOSE_SAMPLE,
            pricing_type=QuickCosting.PRICING_SAMPLE, quantity=2, currency="CAD",
            selling_price_per_piece=Decimal("50"), salesperson=cls.sales,
            status=QuickCosting.STATUS_INVOICED,
            approved_by=cls.ceo, approved_at=timezone.now(),
            quotation_number="QQT-TYPE", quoted_by=cls.ceo, quoted_at=timezone.now(),
        )
        cls.invoice = Invoice.objects.create(
            opportunity=cls.opportunity, quick_costing=cls.quick, customer=cls.customer,
            invoice_number="INV-TYPE", currency="CAD", invoice_region="CA",
            invoice_type="sample", subtotal=100, total_amount=100, paid_amount=0,
        )

    def edit(self, data, *, user=None):
        self.client.force_login(user or self.sales)
        return self.client.post(reverse("opportunity_edit", args=[self.opportunity.pk]), data)

    def financial_snapshot(self):
        models = (
            AccountingEntry, FinanceOperation, FinancialAuditEvent, InvoiceFinancialState,
            InvoicePayment, JournalEntry, JournalLine,
        )
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in models}

    def test_unknown_assignment_stays_unset_without_guessing_in_house(self):
        self.assertEqual(self.opportunity.production_type, "")
        self.assertEqual(self.opportunity.third_party_name, "")
        self.assertEqual(self.opportunity.third_party_notes, "")
        self.opportunity.notes = "Historical assignment not confirmed"
        self.opportunity.save(update_fields=["notes"])
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.production_type, "")

    def test_form_allows_optional_details_and_rejects_unknown_type_and_long_name(self):
        for value in ("", "in_house", "third_party"):
            with self.subTest(value=value):
                form = OpportunityProductionTypeForm({"production_type": value})
                self.assertTrue(form.is_valid(), form.errors)
        invalid = OpportunityProductionTypeForm({"production_type": "automatic"})
        self.assertFalse(invalid.is_valid())
        self.assertIn("production_type", invalid.errors)
        oversized = OpportunityProductionTypeForm({
            "production_type": "third_party", "third_party_name": "X" * 201,
        })
        self.assertFalse(oversized.is_valid())
        self.assertIn("third_party_name", oversized.errors)

    def test_sales_can_save_third_party_details_without_financial_or_release_changes(self):
        before = self.financial_snapshot()
        invoice_before = Invoice.objects.values().get(pk=self.invoice.pk)
        approvals_before = (self.quick.approved_by_id, self.quick.approved_at, self.quick.quoted_by_id, self.quick.quoted_at)
        response = self.edit({
            "production_type": "third_party", "third_party_name": "External workshop",
            "third_party_notes": "Buyer approved partner for embroidery.",
        })
        self.assertRedirects(response, reverse("opportunity_detail", args=[self.opportunity.pk]), fetch_redirect_response=False)
        self.opportunity.refresh_from_db()
        self.quick.refresh_from_db()
        self.assertEqual(self.opportunity.production_type, "third_party")
        self.assertEqual(self.opportunity.third_party_name, "External workshop")
        self.assertEqual(self.opportunity.third_party_notes, "Buyer approved partner for embroidery.")
        self.assertFalse(ProductionOrder.objects.exists())
        self.assertEqual(before, self.financial_snapshot())
        self.assertEqual(invoice_before, Invoice.objects.values().get(pk=self.invoice.pk))
        self.assertEqual(approvals_before, (self.quick.approved_by_id, self.quick.approved_at, self.quick.quoted_by_id, self.quick.quoted_at))

    def test_update_uses_existing_actor_and_field_audit_history(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.edit({
                "production_type": "third_party", "third_party_name": "Audited partner",
                "third_party_notes": "Confirmed by sales.",
            })
        for field, value in (
            ("production_type", "third_party"), ("third_party_name", "Audited partner"),
            ("third_party_notes", "Confirmed by sales."),
        ):
            audit = CRMAuditLog.objects.get(
                module="opportunities", record_id=str(self.opportunity.pk), field_name=field,
            )
            self.assertEqual(audit.actor_id, self.sales.pk)
            self.assertEqual(audit.previous_value, "")
            self.assertEqual(audit.new_value, value)
            self.assertEqual(audit.action_type, CRMAuditLog.ACTION_UPDATED)
            self.assertIsNotNone(audit.created_at)

    def test_other_sales_and_finance_cannot_update_assignment(self):
        for user, expected in ((self.other_sales, 404), (self.finance, 403)):
            with self.subTest(user=user.username):
                response = self.edit({"production_type": "third_party"}, user=user)
                self.assertEqual(response.status_code, expected)
                self.opportunity.refresh_from_db()
                self.assertEqual(self.opportunity.production_type, "")

    def test_legacy_edit_without_selector_preserves_assignment_details(self):
        Opportunity.objects.filter(pk=self.opportunity.pk).update(
            production_type="third_party", third_party_name="Existing partner", third_party_notes="Existing notes",
        )
        self.edit({"notes": "Unrelated ordinary opportunity edit"})
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.production_type, "third_party")
        self.assertEqual(self.opportunity.third_party_name, "Existing partner")
        self.assertEqual(self.opportunity.third_party_notes, "Existing notes")

    def test_invalid_selection_does_not_partially_save_opportunity(self):
        before = Opportunity.objects.values().get(pk=self.opportunity.pk)
        response = self.edit({"production_type": "invalid", "notes": "Should not save"})
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(before, Opportunity.objects.values().get(pk=self.opportunity.pk))

    def test_edit_screen_has_existing_optional_controls(self):
        self.client.force_login(self.sales)
        response = self.client.get(reverse("opportunity_edit", args=[self.opportunity.pk]))
        self.assertContains(response, 'name="production_type"')
        self.assertContains(response, 'value="in_house"')
        self.assertContains(response, 'value="third_party"')
        self.assertContains(response, 'name="third_party_name"')
        self.assertContains(response, 'name="third_party_notes"')

    def test_indicators_appear_on_all_four_existing_screens(self):
        order = ProductionOrder.objects.create(title="Assignment display", opportunity=self.opportunity)
        self.client.force_login(self.ceo)
        for value, label in (("in_house", "IN HOUSE"), ("third_party", "THIRD PARTY")):
            Opportunity.objects.filter(pk=self.opportunity.pk).update(production_type=value)
            for name, args in (
                ("opportunities_list", []), ("opportunity_detail", [self.opportunity.pk]),
                ("production_list", []), ("production_detail", [order.pk]),
            ):
                with self.subTest(value=value, page=name):
                    response = self.client.get(reverse(name, args=args), {"status": "all"})
                    self.assertContains(response, label)

    def test_shared_indicator_has_distinct_colors_and_no_additional_queries(self):
        for value, label, css, color in (
            ("in_house", "IN HOUSE", "in-house", "#14532d"),
            ("third_party", "THIRD PARTY", "third-party", "#581c87"),
            ("", "Unset", "unset", "#334155"),
        ):
            self.opportunity.production_type = value
            with self.subTest(value=value), CaptureQueriesContext(connection) as queries:
                rendered = render_to_string(
                    "crm/partials/production_type_indicator.html",
                    {"production_opportunity": self.opportunity},
                )
            self.assertIn(label, rendered)
            self.assertIn(f"iconic-production-type--{css}", rendered)
            self.assertIn(color, rendered)
            self.assertEqual(len(queries), 0)
            if not value:
                self.assertNotIn("IN HOUSE", rendered)
                self.assertNotIn("THIRD PARTY", rendered)

    def test_many_production_indicators_use_existing_join_without_n_plus_one(self):
        for index in range(12):
            opportunity = Opportunity.objects.create(
                customer=self.customer, assigned_to=self.sales,
                production_type="third_party" if index % 2 else "in_house",
            )
            ProductionOrder.objects.create(title=f"Indicator {index}", opportunity=opportunity)
        orders = list(ProductionOrder.objects.select_related("opportunity"))
        with CaptureQueriesContext(connection) as queries:
            rendered = [
                render_to_string("crm/partials/production_type_indicator.html", {
                    "production_opportunity": order.opportunity,
                }) for order in orders
            ]
        self.assertEqual(len(rendered), 12)
        self.assertEqual(len(queries), 0)

    def test_third_party_details_are_safely_escaped(self):
        Opportunity.objects.filter(pk=self.opportunity.pk).update(
            production_type="third_party", third_party_name='<script>alert("name")</script>',
            third_party_notes='<script>alert("notes")</script>',
        )
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("name")</script>')
        self.assertNotContains(response, '<script>alert("notes")</script>')

    def test_assignment_survives_existing_unpaid_release_and_repeated_release(self):
        self.edit({
            "production_type": "third_party", "third_party_name": "Release partner",
            "third_party_notes": "Production assignment notes.",
        })
        before = self.financial_snapshot()
        invoice_fields = [field.attname for field in Invoice._meta.concrete_fields if field.name not in {"order", "updated_at"}]
        invoice_before = Invoice.objects.values(*invoice_fields).get(pk=self.invoice.pk)
        readiness = production_readiness(self.opportunity, user=self.production)
        self.assertTrue(readiness["ready"])
        self.assertFalse(readiness["payment_verified"])
        self.assertTrue(readiness["costing_approved"])
        self.assertTrue(readiness["quotation_approved"])
        self.client.force_login(self.production)
        url = reverse("production_from_opportunity", args=[self.opportunity.pk])
        response = self.client.post(url, {"action": "release"})
        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.select_related("opportunity").get(opportunity=self.opportunity)
        self.assertEqual(order.opportunity.production_type, "third_party")
        self.assertEqual(order.opportunity.third_party_name, "Release partner")
        self.assertEqual(order.opportunity.third_party_notes, "Production assignment notes.")
        self.assertEqual(order.opportunity.stage, "Production")
        self.client.post(url, {"action": "release"})
        self.assertEqual(ProductionOrder.objects.filter(opportunity=self.opportunity).count(), 1)
        self.assertEqual(before, self.financial_snapshot())
        self.assertEqual(invoice_before, Invoice.objects.values(*invoice_fields).get(pk=self.invoice.pk))

    def test_assignment_does_not_waive_missing_commercial_approvals(self):
        for field, changes in (
            ("costing_approved", {"approved_by": None, "approved_at": None, "status": QuickCosting.STATUS_DRAFT}),
            ("quotation_approved", {"quotation_number": "", "quoted_by": None, "quoted_at": None}),
        ):
            with self.subTest(field=field):
                with self.captureOnCommitCallbacks(execute=False):
                    original = {key: getattr(self.quick, key) for key in changes}
                    QuickCosting.objects.filter(pk=self.quick.pk).update(**changes)
                    Opportunity.objects.filter(pk=self.opportunity.pk).update(production_type="third_party")
                    readiness = production_readiness(self.opportunity, user=self.production)
                    self.assertFalse(readiness[field])
                    self.assertFalse(readiness["ready"])
                    QuickCosting.objects.filter(pk=self.quick.pk).update(**original)
        self.assertFalse(ProductionOrder.objects.exists())
