from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models import Lead, Opportunity
from crm.services.employee_identity import (
    get_employee_identity_index,
    resolve_lead_owner,
)
from crm.services.operations_permissions import scope_sales_leads
from crm.services.operations_search import search_operations_records
from crm.services.sales_profiles import build_salesperson_profile, build_team_performance


class CanonicalEmployeeOwnershipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.sales_group = Group.objects.get_or_create(name="Sales")[0]
        cls.ceo_group = Group.objects.get_or_create(name="CEO")[0]

        cls.hossein = User.objects.create_user(
            "hossein-owner", first_name="Hossain", last_name="Forhad"
        )
        cls.hossein.groups.add(cls.sales_group)
        cls.hossein.employee_profile.display_name = "Hossain"
        cls.hossein.employee_profile.aliases = ["Hossein", "Hossein Farhad", "Hussain", "Farhad"]
        cls.hossein.employee_profile.save()

        cls.refat = User.objects.create_user(
            "refat-owner", first_name="Md", last_name="Refat"
        )
        cls.refat.groups.add(cls.sales_group)
        cls.refat.employee_profile.display_name = "Md Refat"
        cls.refat.employee_profile.aliases = ["Refat", "Rifat"]
        cls.refat.employee_profile.save()

        cls.talha = User.objects.create_user(
            "talha-owner", first_name="Talha", last_name="Akbar"
        )
        cls.talha.groups.add(cls.sales_group)
        cls.talha.employee_profile.display_name = "Talha Akbar"
        cls.talha.employee_profile.aliases = ["Talha"]
        cls.talha.employee_profile.save()

        cls.ceo = User.objects.create_user("owner-ceo", first_name="Owner", last_name="CEO")
        cls.ceo.groups.add(cls.ceo_group)

        cls.hossein_direct = Lead.objects.create(
            account_brand="Hossein Direct",
            assigned_to=cls.hossein,
            owner="Rifat",
            lead_type="outbound",
        )
        cls.hossein_alias = Lead.objects.create(
            account_brand="Hossein Alias",
            owner="Hossein",
            lead_type="outbound",
        )
        cls.hossein_canonical = Lead.objects.create(
            account_brand="Hossein Canonical",
            owner="Hossein Farhad",
            lead_type="outbound",
        )
        cls.refat_alias = Lead.objects.create(
            account_brand="Refat Alias",
            owner="Rifat",
            lead_type="outbound",
        )
        cls.refat_active_alias = Lead.objects.create(
            account_brand="Refat Active Alias",
            owner="Refat",
            lead_type="outbound",
        )
        cls.talha_alias = Lead.objects.create(
            account_brand="Talha Alias",
            owner="Talha",
            lead_type="outbound",
        )
        cls.unmatched = Lead.objects.create(
            account_brand="Legacy Unknown",
            owner="Former Salesperson",
            lead_type="outbound",
        )
        Opportunity.objects.create(
            lead=cls.refat_alias,
            stage="Closed Won",
            is_open=False,
            order_currency="CAD",
            order_value=Decimal("2500"),
        )

    def setUp(self):
        cache.clear()
        self.client = Client()

    def test_alias_resolution_prioritizes_assigned_user_and_does_not_rewrite_history(self):
        self.assertEqual(resolve_lead_owner(self.hossein_direct)["canonical_name"], "Hossain")
        self.assertEqual(resolve_lead_owner(self.hossein_alias)["canonical_name"], "Hossain")
        self.assertEqual(resolve_lead_owner(self.refat_alias)["canonical_name"], "Md Refat")
        self.assertEqual(resolve_lead_owner(self.talha_alias)["canonical_name"], "Talha Akbar")
        self.refat_alias.refresh_from_db()
        self.assertEqual(self.refat_alias.owner, "Rifat")

    def test_sales_scope_and_personal_metrics_include_alias_owned_leads(self):
        visible_ids = set(scope_sales_leads(Lead.objects.all(), self.hossein).values_list("pk", flat=True))
        self.assertIn(self.hossein_direct.pk, visible_ids)
        self.assertIn(self.hossein_alias.pk, visible_ids)
        self.assertIn(self.hossein_canonical.pk, visible_ids)
        self.assertNotIn(self.refat_alias.pk, visible_ids)
        metrics = build_salesperson_profile(self.hossein)
        self.assertEqual(metrics["lead_counts"]["total"], 3)

    def test_lead_list_filter_uses_canonical_employee_identity(self):
        self.client.force_login(self.ceo)
        response = self.client.get(
            reverse("leads_list"),
            {"assigned_to": self.refat.pk},
        )
        self.assertEqual(response.status_code, 200)
        listed_ids = {lead.pk for lead in response.context["page_obj"].object_list}
        self.assertEqual(listed_ids, {self.refat_active_alias.pk})
        self.assertNotContains(response, "Hossain Forhad")
        self.assertContains(response, "Hossain")

    def test_lead_dashboard_groups_aliases_into_one_canonical_card(self):
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("leads_dashboard"))
        self.assertEqual(response.status_code, 200)
        grouped = {
            row["assigned_to__first_name"]: row["count"]
            for row in response.context["by_assigned"]
        }
        self.assertEqual(grouped["Hossain"], 3)
        self.assertEqual(grouped["Md Refat"], 1)
        self.assertEqual(grouped["Talha Akbar"], 1)
        self.assertEqual(sum(grouped.values()), 6)

    def test_team_workload_and_reports_use_canonical_identity(self):
        metrics = build_team_performance()
        rows = {row["name"]: row for row in metrics["team_rows"]}
        self.assertEqual(set(rows), {"Hossain", "Md Refat", "Talha Akbar"})
        self.assertEqual(rows["Md Refat"]["won"], 1)
        self.assertEqual(rows["Md Refat"]["revenue"]["CAD"], Decimal("2500"))

    def test_alias_search_returns_only_the_canonical_employee(self):
        results = dict(search_operations_records(self.ceo, "Rifat"))
        self.assertEqual(len(results["Employees"]), 1)
        self.assertEqual(results["Employees"][0]["name"], "Md Refat")

        self.client.force_login(self.ceo)
        response = self.client.get(reverse("employee_list"), {"q": "Talha"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([profile.public_name for profile in response.context["profiles"]], ["Talha Akbar"])

        hossein_response = self.client.get(reverse("employee_list"), {"q": "Hossein"})
        self.assertEqual(hossein_response.status_code, 200)
        self.assertContains(hossein_response, "Hossain")
        self.assertNotContains(hossein_response, "Hossein Farhad")

    def test_employee_management_saves_aliases_and_rejects_identity_conflicts(self):
        self.client.force_login(self.ceo)
        edit_page = self.client.get(reverse("employee_edit", args=[self.talha.pk]))
        self.assertEqual(edit_page.context["form"]["aliases"].value(), "Talha")
        response = self.client.post(
            reverse("employee_edit", args=[self.talha.pk]),
            {
                "username": self.talha.username,
                "full_name": "Talha Akbar",
                "display_name": "Talha Akbar",
                "aliases": "Talha\nT Akbar, TA",
                "position": "sales_executive",
                "department": "sales",
                "status": "active",
                "is_active": "on",
                "roles": [self.sales_group.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.talha.employee_profile.refresh_from_db()
        self.assertEqual(self.talha.employee_profile.aliases, ["Talha", "T Akbar", "TA"])

        conflict = self.client.post(
            reverse("employee_edit", args=[self.refat.pk]),
            {
                "username": self.refat.username,
                "full_name": "Md Refat",
                "display_name": "Md Refat",
                "aliases": "Hossein",
                "position": "sales_executive",
                "department": "sales",
                "status": "active",
                "is_active": "on",
                "roles": [self.sales_group.pk],
            },
        )
        self.assertEqual(conflict.status_code, 200)
        self.assertContains(conflict, "already identify another employee")

    def test_identity_mapping_is_loaded_once_and_reused(self):
        with CaptureQueriesContext(connection) as first_queries:
            index = get_employee_identity_index(force_refresh=True)
        self.assertEqual(len(first_queries), 1)
        with CaptureQueriesContext(connection) as cached_queries:
            self.assertEqual(resolve_lead_owner(self.refat_alias, index=index)["canonical_name"], "Md Refat")
            self.assertEqual(get_employee_identity_index()["by_user_id"][self.talha.pk]["canonical_name"], "Talha Akbar")
        self.assertEqual(len(cached_queries), 0)
