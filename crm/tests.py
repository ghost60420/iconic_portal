from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.test import TestCase
from django.urls import reverse

from crm.models import Lead, Opportunity
from crm.views import _production_library_context


class OpportunityStatusTests(TestCase):
    def setUp(self):
        self.lead = Lead.objects.create(account_brand="Test Brand")

    def test_opportunity_status_label(self):
        opp_open = Opportunity.objects.create(
            lead=self.lead,
            stage="Prospecting",
            is_open=True,
        )
        opp_won = Opportunity.objects.create(
            lead=self.lead,
            stage="Closed Won",
            is_open=False,
        )
        opp_lost = Opportunity.objects.create(
            lead=self.lead,
            stage="Closed Lost",
            is_open=False,
        )

        self.assertEqual(opp_open.status_label, "Open")
        self.assertEqual(opp_won.status_label, "Closed Won")
        self.assertEqual(opp_lost.status_label, "Closed Lost")


class ProductionLibraryContextTests(TestCase):
    def test_missing_relation_tables_return_empty_context(self):
        broken_relation = Mock()
        broken_relation.all.side_effect = OperationalError("missing relation table")
        order = Mock(
            fabrics=broken_relation,
            accessories=broken_relation,
            trims=broken_relation,
            threads=broken_relation,
        )

        with patch("crm.views.Product.objects.filter", side_effect=OperationalError("missing product table")):
            with patch("crm.views.Fabric.objects.filter", side_effect=OperationalError("missing fabric table")):
                with patch("crm.views.Accessory.objects.filter", side_effect=OperationalError("missing accessory table")):
                    with patch("crm.views.Trim.objects.filter", side_effect=OperationalError("missing trim table")):
                        with patch("crm.views.ThreadOption.objects.filter", side_effect=OperationalError("missing thread table")):
                            context = _production_library_context(order)

        self.assertEqual(context["selected_fabrics"], [])
        self.assertEqual(context["selected_accessories"], [])
        self.assertEqual(context["selected_trims"], [])
        self.assertEqual(context["selected_threads"], [])
        self.assertEqual(list(context["library_products"]), [])
        self.assertEqual(list(context["library_fabrics"]), [])
        self.assertEqual(list(context["library_accessories"]), [])
        self.assertEqual(list(context["library_trims"]), [])
        self.assertEqual(list(context["library_threads"]), [])


class MainDashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="dashboard-user", password="pass1234")
        self.client.force_login(self.user)

    def test_dashboard_renders_redesigned_context(self):
        response = self.client.get(reverse("main_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/main_dashboard.html")
        self.assertContains(response, "CRM Dashboard")
        self.assertIn("primary_kpis", response.context)
        self.assertEqual(len(response.context["primary_kpis"]), 6)
        self.assertIn("finance_summary_cards", response.context)
        self.assertIn("dashboard_alerts", response.context)
        self.assertIn("recent_leads", response.context)
        self.assertIn("action_recommendations", response.context)

        chart_data = response.context["chart_data"]
        self.assertIn("lead_fit_labels", chart_data)
        self.assertIn("monthly_profit_labels", chart_data)
        self.assertIn("invoice_status_labels", chart_data)
