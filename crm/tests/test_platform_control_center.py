from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from crm.forms_employee import EmployeeProfileForm
from crm.models import (
    CostingHeader,
    Customer,
    Department,
    FavoriteRecord,
    Invoice,
    Lead,
    Opportunity,
    Position,
    ProductionOrder,
    RecentlyViewedRecord,
    SavedFilter,
    UserDashboardPreference,
)
from crm.permissions import role_flag_decision
from crm.services.operations_permissions import can_access_operations_module
from crm.services.platform_tools import record_descriptor, track_recent_record


class PlatformControlCenterTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.ceo = self.User.objects.create_user("platform-ceo", password="test-pass", first_name="Hossein")
        self.sales = self.User.objects.create_user("platform-sales", password="test-pass", first_name="Talha")
        self.other = self.User.objects.create_user("platform-other", password="test-pass", first_name="Other")
        self.admin = self.User.objects.create_user("platform-admin", password="test-pass", first_name="Admin")
        for name, user in (("CEO", self.ceo), ("Sales", self.sales), ("Sales", self.other), ("Admin", self.admin)):
            Group.objects.get_or_create(name=name)[0].user_set.add(user)
        self.customer = Customer.objects.create(account_brand="Platform Customer", contact_name="Buyer")
        self.lead = Lead.objects.create(account_brand="Platform Customer", assigned_to=self.sales, customer=self.customer)
        self.other_lead = Lead.objects.create(account_brand="Restricted Customer", assigned_to=self.other)
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            order_currency="CAD",
            order_value=Decimal("1000.00"),
        )
        self.quotation = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            order_quantity=10,
            currency="CAD",
            manual_fob_per_piece=Decimal("25.00"),
            quoted_by=self.sales,
        )
        self.production = ProductionOrder.objects.create(
            title="Platform order",
            order_code="PO-PLATFORM-1",
            customer=self.customer,
            lead=self.lead,
            opportunity=self.opportunity,
        )
        self.invoice = Invoice.objects.create(
            invoice_number="INV-PLATFORM-1",
            customer=self.customer,
            order=self.production,
            currency="CAD",
            total_amount=Decimal("250.00"),
        )

    def test_master_libraries_are_seeded_and_employee_form_uses_them(self):
        self.assertTrue(Position.objects.filter(name="CEO", is_active=True).exists())
        self.assertTrue(Position.objects.filter(name="Factory Manager", is_active=True).exists())
        self.assertTrue(Department.objects.filter(name="Sales", is_active=True).exists())
        form = EmployeeProfileForm(user_instance=self.sales)
        self.assertEqual(form.fields["position_ref"].queryset.model, Position)
        self.assertEqual(form.fields["department_ref"].queryset.model, Department)
        self.assertNotIn("position", form.fields)
        self.assertNotIn("department", form.fields)

    def test_multiple_roles_are_additive_through_one_permission_service(self):
        Group.objects.get_or_create(name="Production")[0].user_set.add(self.sales)
        self.sales._operations_group_names = None
        self.assertTrue(can_access_operations_module(self.sales, "leads"))
        self.assertTrue(can_access_operations_module(self.sales, "production"))
        self.assertFalse(can_access_operations_module(self.sales, "finance"))
        self.assertTrue(role_flag_decision(self.sales, "can_leads"))
        self.assertTrue(role_flag_decision(self.sales, "can_production"))

    def test_dashboard_preferences_are_user_owned_and_ceo_layout_is_fixed(self):
        client = Client()
        client.force_login(self.sales)
        response = client.post(
            reverse("dashboard_preferences"),
            data='{"hidden":["notifications"],"order":["recent","kpis"]}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        preference = UserDashboardPreference.objects.get(user=self.sales)
        self.assertEqual(preference.hidden_widgets, ["notifications"])
        self.assertEqual(preference.widget_order, ["recent", "kpis"])

        client.force_login(self.ceo)
        self.assertEqual(
            client.post(reverse("dashboard_preferences"), data="{}", content_type="application/json").status_code,
            403,
        )

    def test_saved_filters_are_private_to_the_user(self):
        client = Client()
        client.force_login(self.sales)
        response = client.post(reverse("saved_filter_save"), {"module": "leads", "name": "My Open Leads", "status": "New"})
        self.assertEqual(response.status_code, 200)
        row = SavedFilter.objects.get(user=self.sales)
        self.assertEqual(row.query_params["status"], ["New"])
        self.assertFalse(SavedFilter.objects.filter(user=self.other).exists())

    def test_favorites_and_recent_records_enforce_sales_scope(self):
        client = Client()
        client.force_login(self.sales)
        allowed = client.post(reverse("favorite_toggle", args=("lead", self.lead.pk)))
        denied = client.post(reverse("favorite_toggle", args=("lead", self.other_lead.pk)))
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(FavoriteRecord.objects.filter(user=self.sales).count(), 1)

        for index in range(25):
            customer = Customer.objects.create(account_brand=f"Recent {index}")
            track_recent_record(self.ceo, record_descriptor(self.ceo, "customer", customer.pk))
        self.assertEqual(RecentlyViewedRecord.objects.filter(user=self.ceo).count(), 20)
        self.assertFalse(RecentlyViewedRecord.objects.filter(user=self.sales).exists())

    def test_global_search_supports_employees_and_recent_items_without_leaking_records(self):
        client = Client()
        client.force_login(self.ceo)
        employee_response = client.get(reverse("global_search_suggestions"), {"q": "Talha"})
        self.assertEqual(employee_response.status_code, 200)
        self.assertIn("Employees", [group["label"] for group in employee_response.json()["groups"]])
        client.get(reverse("global_search"), {"q": "Platform Customer"})
        recent = client.get(reverse("global_search_suggestions"), {"q": ""})
        self.assertIn("Recent Searches", [group["label"] for group in recent.json()["groups"]])

        client.force_login(self.sales)
        restricted = client.get(reverse("global_search_suggestions"), {"q": "Restricted Customer"})
        result_names = [row["name"] for group in restricted.json()["groups"] for row in group["rows"]]
        self.assertNotIn("Restricted Customer", result_names)

    def test_uncovered_records_archive_and_restore_without_deletion(self):
        client = Client()
        client.force_login(self.ceo)
        for record_type, record in (("customer", self.customer), ("quotation", self.quotation), ("invoice", self.invoice)):
            response = client.post(reverse("archive_record", args=(record_type, record.pk)), {"action": "archive"})
            self.assertEqual(response.status_code, 302)
            record.refresh_from_db()
            self.assertTrue(record.is_archived)
            response = client.post(reverse("archive_record", args=(record_type, record.pk)), {"action": "restore"})
            self.assertEqual(response.status_code, 302)
            record.refresh_from_db()
            self.assertFalse(record.is_archived)
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

        client.force_login(self.sales)
        self.assertEqual(
            client.post(reverse("archive_record", args=("customer", self.customer.pk)), {"action": "archive"}).status_code,
            403,
        )

    def test_legacy_delete_endpoints_archive_instead_of_hard_delete(self):
        client = Client()
        client.force_login(self.ceo)
        unlinked_lead = Lead.objects.create(account_brand="Archive-only lead")
        response = client.post(reverse("lead_delete", args=[unlinked_lead.pk]))
        self.assertEqual(response.status_code, 302)
        unlinked_lead.refresh_from_db()
        self.assertTrue(unlinked_lead.is_archived)

        opportunity = Opportunity.objects.create(lead=unlinked_lead, order_currency="CAD")
        response = client.post(reverse("opportunity_delete", args=[opportunity.pk]), {"workflow_action": "delete"})
        self.assertEqual(response.status_code, 302)
        opportunity.refresh_from_db()
        self.assertTrue(opportunity.is_archived)

        order = ProductionOrder.objects.create(title="Archive-only production", order_code="PO-ARCHIVE-ONLY")
        response = client.post(reverse("production_delete", args=[order.pk]))
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertTrue(order.is_archived)

    def test_settings_and_system_health_are_server_restricted(self):
        client = Client()
        client.force_login(self.sales)
        self.assertEqual(client.get(reverse("crm_settings")).status_code, 403)
        self.assertEqual(client.get(reverse("system_health")).status_code, 403)
        client.force_login(self.admin)
        self.assertEqual(client.get(reverse("crm_settings")).status_code, 200)
        self.assertEqual(client.get(reverse("system_health")).status_code, 403)
        client.force_login(self.ceo)
        self.assertEqual(client.get(reverse("system_health")).status_code, 200)

    def test_global_search_query_count_is_bounded(self):
        client = Client()
        client.force_login(self.ceo)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(reverse("global_search_suggestions"), {"q": "Platform"})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 20)
