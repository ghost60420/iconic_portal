from unittest.mock import Mock, patch
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import AccountingEntry, Customer, Invoice, Lead, Opportunity
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
        primary_kpi_titles = [card["title"] for card in response.context["primary_kpis"]]
        self.assertEqual(len(response.context["primary_kpis"]), 6)
        self.assertIn("Awaiting Payment Orders", primary_kpi_titles)
        self.assertNotIn("Monthly Profit", primary_kpi_titles)
        self.assertIn("finance_summary_cards", response.context)
        self.assertIn("dashboard_alerts", response.context)
        self.assertIn("recent_leads", response.context)
        self.assertIn("action_recommendations", response.context)

        chart_data = response.context["chart_data"]
        self.assertIn("lead_fit_labels", chart_data)
        self.assertNotIn("monthly_profit_labels", chart_data)
        self.assertIn("invoice_status_labels", chart_data)


class FinancialDashboardUiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="finance-admin",
            email="finance-admin@example.com",
            password="pass1234",
        )
        self.client.force_login(self.user)

    def test_accounts_receivable_dashboard_shows_aging_buckets(self):
        today = timezone.localdate()
        customer = Customer.objects.create(account_brand="Aging Client")
        Invoice.objects.create(
            invoice_number="INV-AGING-001",
            customer=customer,
            issue_date=today - timedelta(days=70),
            due_date=today - timedelta(days=40),
            currency="CAD",
            invoice_region="CA",
            invoice_market="north_america",
            subtotal=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("100.00"),
            status="partial",
        )

        response = self.client.get(reverse("accounts_receivable_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AR aging buckets")
        aging_rows = response.context["aging_rows"]
        bucket = next(row for row in aging_rows if row["label"] == "31-60 days")
        self.assertEqual(bucket["invoice_count"], 1)
        self.assertEqual(bucket["currency_totals"][0]["amount"], Decimal("900.00"))

    def test_accounts_payable_dashboard_shows_aging_buckets(self):
        today = timezone.localdate()
        AccountingEntry.objects.create(
            date=today - timedelta(days=40),
            side=AccountingEntry.SIDE_CA,
            direction=AccountingEntry.DIR_OUT,
            status="",
            main_type="EXPENSE",
            sub_type="Rent",
            currency="CAD",
            amount_original=Decimal("250.00"),
            rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("90"),
        )

        response = self.client.get(reverse("accounts_payable_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AP aging buckets")
        aging_rows = response.context["aging_rows"]
        bucket = next(row for row in aging_rows if row["label"] == "31-60 days")
        self.assertEqual(bucket["bill_count"], 1)
        self.assertEqual(bucket["currency_totals"][0]["amount"], Decimal("250.00"))
