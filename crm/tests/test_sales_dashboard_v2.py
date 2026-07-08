import inspect
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    Customer,
    CRMAuditLog,
    Invoice,
    InvoicePayment,
    Lead,
    Opportunity,
    ProductionOrder,
    QuickCosting,
    SalesCommission,
    Shipment,
)
from crm.services.sales_attribution import attribution_for, build_sales_kpis


class SalesDashboardV2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        sales = Group.objects.get_or_create(name="Sales")[0]
        cls.owner = User.objects.create_user("refat-v2", first_name="Md", last_name="Refat")
        cls.owner.groups.add(sales)
        cls.owner.employee_profile.display_name = "Md Refat"
        cls.owner.employee_profile.save()
        cls.author = User.objects.create_user("hossain-v2", first_name="Hossain", last_name="Farhad")
        cls.author.groups.add(sales)
        cls.author.employee_profile.display_name = "Hossain Farhad"
        cls.author.employee_profile.save()

        cls.customer = Customer.objects.create(account_brand="Attribution Customer")
        cls.lead = Lead.objects.create(
            account_brand="Attribution Customer",
            assigned_to=cls.owner,
            customer=cls.customer,
            lead_status="Converted",
        )
        cls.opportunity = Opportunity.objects.create(
            lead=cls.lead,
            customer=cls.customer,
            stage="Proposal",
            order_currency="CAD",
            order_value=Decimal("1200"),
        )
        cls.quick = QuickCosting.objects.create(
            opportunity=cls.opportunity,
            buyer_name="Buyer",
            project_name="Project",
            quantity=100,
            currency="CAD",
            selling_price_per_piece=Decimal("12"),
            quotation_number="QT-V2",
            status=QuickCosting.STATUS_QUOTED,
            created_by=cls.author,
            quoted_by=cls.author,
        )
        cls.order = ProductionOrder.objects.create(
            title="Attributed production",
            opportunity=cls.opportunity,
            customer=cls.customer,
            created_by=cls.author,
            approved_currency="CAD",
            approved_total_value=Decimal("1200"),
            status="in_progress",
        )
        cls.invoice = Invoice.objects.create(
            invoice_number="INV-SALES-V2",
            quick_costing=cls.quick,
            customer=cls.customer,
            currency="CAD",
            total_amount=Decimal("1200"),
            paid_amount=Decimal("900"),
            status="sent",
        )
        InvoicePayment.objects.create(
            invoice=cls.invoice,
            payment_date="2026-07-01",
            amount=Decimal("300"),
            currency="CAD",
            created_by=cls.author,
        )
        SalesCommission.objects.create(
            invoice=cls.invoice,
            eligible_amount=Decimal("1200"),
            currency="CAD",
            commission_percent=Decimal("5"),
        )

    def test_salesperson_and_author_are_separate(self):
        attribution = attribution_for(self.quick)
        self.assertEqual(attribution["salesperson"]["canonical_name"], "Md Refat")
        self.assertEqual(attribution["author"]["canonical_name"], "Hossain Farhad")

    def test_missing_direct_author_uses_existing_audit_creator(self):
        CRMAuditLog.objects.create(
            actor=self.author,
            module="invoices",
            record_id=str(self.invoice.pk),
            record_label=self.invoice.invoice_number,
            action_type=CRMAuditLog.ACTION_CREATED,
        )
        attribution = attribution_for(self.invoice)
        self.assertEqual(attribution["salesperson"]["canonical_name"], "Md Refat")
        self.assertEqual(attribution["author"]["canonical_name"], "Hossain Farhad")

    def test_metrics_follow_lead_and_payment_history(self):
        metrics = build_sales_kpis(self.owner)
        pipeline = {row["currency"]: row["amount"] for row in metrics["pipeline_value"]}
        invoiced = {row["currency"]: row["amount"] for row in metrics["invoice_values"]}
        collected = {row["currency"]: row["amount"] for row in metrics["collected_values"]}
        production = {row["currency"]: row["amount"] for row in metrics["production_values"]}
        commissions = {row["currency"]: row["amount"] for row in metrics["commission_values"]}
        self.assertEqual(pipeline, {"CAD": Decimal("0"), "USD": Decimal("0"), "BDT": Decimal("0")})
        self.assertEqual(metrics["total_order_value"][0]["amount"], Decimal("1200"))
        self.assertEqual(invoiced["CAD"], Decimal("1200"))
        self.assertEqual(collected["CAD"], Decimal("300"))
        self.assertNotEqual(collected["CAD"], self.invoice.paid_amount)
        self.assertEqual(production["CAD"], Decimal("1200"))
        self.assertEqual(commissions["CAD"], Decimal("60.00"))

    def test_closed_records_are_not_pipeline(self):
        self.opportunity.stage = "Closed Won"
        self.opportunity.is_open = False
        self.opportunity.save(update_fields=["stage", "is_open"])
        metrics = build_sales_kpis(self.owner)
        self.assertEqual(metrics["pipeline_count"], 0)
        self.assertEqual(metrics["closed_won_count"], 1)

    def test_dashboard_service_has_no_n_plus_one_and_meets_budget(self):
        with CaptureQueriesContext(connection) as queries:
            build_sales_kpis(self.owner)
        self.assertLessEqual(len(queries), 10)

    def test_commission_rejects_currency_mismatch(self):
        commission = SalesCommission(
            invoice=self.invoice,
            eligible_amount=Decimal("100"),
            currency="USD",
            commission_percent=Decimal("5"),
        )
        with self.assertRaisesMessage(Exception, "Commission currency must match"):
            commission.save()


class SalesOwnerMetricsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        sales = Group.objects.get_or_create(name="Sales")[0]
        ceo = Group.objects.get_or_create(name="CEO")[0]
        cls.talha = User.objects.create_user("talha-owner-metrics", first_name="Talha", last_name="Akbar")
        cls.talha.groups.add(sales)
        cls.talha.employee_profile.display_name = "Talha Akbar"
        cls.talha.employee_profile.aliases = ["Talha"]
        cls.talha.employee_profile.save()
        cls.biplob = User.objects.create_user("biplob-owner-metrics", first_name="Biplob", last_name="Ahmed")
        cls.biplob.groups.add(sales)
        cls.biplob.employee_profile.display_name = "Biplob"
        cls.biplob.employee_profile.save()
        cls.ceo = User.objects.create_user("sales-owner-ceo", first_name="Owner", last_name="CEO")
        cls.ceo.groups.add(ceo)

        cls.customer = Customer.objects.create(account_brand="Talha Brand", contact_name="Talha Buyer")
        cls.other_customer = Customer.objects.create(account_brand="Biplob Brand")

        cls.active_lead = Lead.objects.create(
            account_brand="Talha Active Lead",
            customer=cls.customer,
            assigned_to=cls.talha,
            lead_status="New",
        )
        cls.converted_lead = Lead.objects.create(
            account_brand="Talha Converted Lead",
            customer=cls.customer,
            assigned_to=cls.talha,
            lead_status="Converted",
        )
        cls.active_opp_lead = Lead.objects.create(
            account_brand="Talha Active Opp Lead",
            customer=cls.customer,
            assigned_to=cls.talha,
            lead_status="Converted",
        )
        cls.active_opportunity = Opportunity.objects.create(
            lead=cls.active_opp_lead,
            customer=cls.customer,
            stage="Proposal",
            is_open=True,
            order_currency="CAD",
            order_value=Decimal("3000"),
        )
        cls.production_lead = Lead.objects.create(
            account_brand="Talha Production Lead",
            customer=cls.customer,
            assigned_to=cls.talha,
            lead_status="Converted",
        )
        cls.production_opportunity = Opportunity.objects.create(
            lead=cls.production_lead,
            customer=cls.customer,
            stage="Production",
            is_open=True,
            order_currency="CAD",
            order_value=Decimal("5000"),
        )
        cls.active_order = ProductionOrder.objects.create(
            title="Talha Active Production",
            opportunity=cls.production_opportunity,
            customer=cls.customer,
            approved_currency="CAD",
            approved_total_value=Decimal("5000"),
            operational_status="sewing",
            qty_total=100,
        )
        cls.ready_order = ProductionOrder.objects.create(
            title="Talha Ready Production",
            lead=cls.production_lead,
            customer=cls.customer,
            approved_currency="CAD",
            approved_total_value=Decimal("1800"),
            operational_status="ready_to_ship",
            qty_total=40,
        )
        cls.shipped_order = ProductionOrder.objects.create(
            title="Talha Shipped Production",
            lead=cls.production_lead,
            customer=cls.customer,
            approved_currency="CAD",
            approved_total_value=Decimal("1200"),
            operational_status="shipped",
            qty_total=20,
        )
        cls.completed_order = ProductionOrder.objects.create(
            title="Talha Completed Production",
            lead=cls.production_lead,
            customer=cls.customer,
            approved_currency="CAD",
            approved_total_value=Decimal("900"),
            operational_status="shipped",
            status="done",
            qty_total=10,
        )
        ProductionOrder.objects.filter(pk=cls.ready_order.pk).update(operational_status="ready_to_ship")
        ProductionOrder.objects.filter(pk=cls.shipped_order.pk).update(operational_status="shipped")
        ProductionOrder.objects.filter(pk=cls.completed_order.pk).update(operational_status="shipped", status="done")
        cls.ready_order.refresh_from_db()
        cls.shipped_order.refresh_from_db()
        cls.completed_order.refresh_from_db()
        Shipment.objects.create(
            order=cls.completed_order,
            customer=cls.customer,
            status="delivered",
            delivered_at=timezone.now(),
            ship_date=timezone.localdate(),
        )
        cls.open_invoice = Invoice.objects.create(
            invoice_number="INV-TALHA-OPEN",
            customer=cls.customer,
            order=cls.active_order,
            currency="CAD",
            total_amount=Decimal("1000"),
            status="sent",
        )
        InvoicePayment.objects.create(
            invoice=cls.open_invoice,
            production_order=cls.active_order,
            amount=Decimal("250"),
            currency="CAD",
        )
        cls.paid_invoice = Invoice.objects.create(
            invoice_number="INV-TALHA-PAID",
            customer=cls.customer,
            order=cls.ready_order,
            currency="CAD",
            total_amount=Decimal("500"),
            status="paid",
        )
        InvoicePayment.objects.create(
            invoice=cls.paid_invoice,
            production_order=cls.ready_order,
            amount=Decimal("500"),
            currency="CAD",
        )

        cls.other_lead = Lead.objects.create(
            account_brand="Biplob Lead",
            customer=cls.other_customer,
            assigned_to=cls.biplob,
            lead_status="New",
        )
        cls.other_invoice = Invoice.objects.create(
            invoice_number="INV-BIPLOB",
            customer=cls.other_customer,
            currency="CAD",
            total_amount=Decimal("700"),
            status="sent",
            opportunity=Opportunity.objects.create(
                lead=cls.other_lead,
                customer=cls.other_customer,
                stage="Proposal",
                is_open=True,
                order_currency="CAD",
                order_value=Decimal("700"),
            ),
        )

    def test_talha_owner_metrics_resolve_linked_records(self):
        metrics = build_sales_kpis(self.talha)
        self.assertEqual(metrics["owner_counts"]["active_leads"], 1)
        self.assertEqual(metrics["owner_counts"]["converted_leads"], 3)
        self.assertEqual(metrics["owner_counts"]["active_opportunities"], 1)
        self.assertEqual(metrics["owner_counts"]["opportunities_moved_to_production"], 1)
        self.assertEqual(metrics["owner_counts"]["active_production_orders"], 1)
        self.assertEqual(metrics["owner_counts"]["ready_to_ship_orders"], 1)
        self.assertEqual(metrics["owner_counts"]["shipped_orders"], 1)
        self.assertEqual(metrics["owner_counts"]["completed_orders"], 1)
        self.assertEqual(metrics["owner_counts"]["open_invoices"], 1)
        self.assertEqual(metrics["owner_counts"]["paid_invoices"], 1)
        self.assertEqual(metrics["total_order_value"][0]["amount"], Decimal("8000"))
        self.assertEqual(metrics["total_invoice_value"][0]["amount"], Decimal("1500"))
        self.assertEqual(metrics["outstanding_balance"][0]["amount"], Decimal("750"))
        self.assertEqual(metrics["pipeline_value"][0]["amount"], Decimal("3000"))
        self.assertEqual(metrics["production_counts"]["active"], 1)
        self.assertEqual(metrics["production_counts"]["ready_to_ship"], 1)
        self.assertEqual(metrics["production_counts"]["shipped"], 1)
        self.assertEqual(metrics["production_counts"]["completed"], 1)

    def test_salesperson_chart_data_is_scoped_and_currency_separated(self):
        today = timezone.localdate()
        self.active_opportunity.product_type = "Streetwear"
        self.active_opportunity.save(update_fields=["product_type"])
        self.production_opportunity.product_type = "Streetwear"
        self.production_opportunity.save(update_fields=["product_type"])
        Invoice.objects.create(
            invoice_number="INV-TALHA-USD-CHART",
            customer=self.customer,
            opportunity=self.active_opportunity,
            currency="USD",
            total_amount=Decimal("250"),
            status="sent",
            issue_date=today,
        )
        Invoice.objects.create(
            invoice_number="INV-TALHA-BDT-CHART",
            customer=self.customer,
            order=self.active_order,
            currency="BDT",
            total_amount=Decimal("250000"),
            status="sent",
            issue_date=today,
        )
        Invoice.objects.create(
            invoice_number="INV-BIPLOB-USD-CHART",
            customer=self.other_customer,
            opportunity=self.other_invoice.opportunity,
            currency="USD",
            total_amount=Decimal("999"),
            status="sent",
            issue_date=today,
        )

        charts = build_sales_kpis(self.talha)["sales_charts"]
        revenue_series = {row["currency"]: row for row in charts["monthly_revenue"]["series"]}
        self.assertTrue(charts["monthly_revenue"]["has_data"])
        self.assertIn(Decimal("250"), [point["amount"] for point in revenue_series["USD"]["points_meta"]])
        self.assertIn(Decimal("250000"), [point["amount"] for point in revenue_series["BDT"]["points_meta"]])
        self.assertNotIn(Decimal("999"), [point["amount"] for point in revenue_series["USD"]["points_meta"]])
        self.assertEqual(
            [item["label"] for item in charts["pipeline_distribution"]["items"]],
            ["Active Leads", "Opportunities", "Production", "Ready to Ship", "Shipped"],
        )
        product_rows = {row["label"]: row for row in charts["revenue_by_product_type"]["rows"]}
        self.assertIn("Streetwear", product_rows)
        streetwear = product_rows["Streetwear"]
        self.assertEqual(
            {bar["currency"]: bar["amount"] for bar in streetwear["bars"]},
            {"CAD": Decimal("1000"), "USD": Decimal("250"), "BDT": Decimal("250000")},
        )

    def test_salesperson_dashboard_renders_charts_below_kpis(self):
        self.client.force_login(self.talha)
        response = self.client.get(reverse("salesperson_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Leads")
        self.assertContains(response, "Monthly Revenue Trend")
        self.assertContains(response, "Pipeline Distribution")
        self.assertContains(response, "Monthly Orders")
        self.assertContains(response, "Revenue by Product Type")
        self.assertContains(response, f"salesperson={self.talha.pk}")

    def test_sales_user_profile_shows_own_numbers_only(self):
        self.client.force_login(self.talha)
        response = self.client.get(reverse("salesperson_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["owner_counts"]["active_leads"], 1)
        self.assertEqual(response.context["total_invoice_value"][0]["amount"], Decimal("1500"))
        self.assertNotContains(response, "INV-BIPLOB")

    def test_ceo_team_dashboard_includes_talha_owner_metrics(self):
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("team_performance"))
        self.assertEqual(response.status_code, 200)
        rows = {row["name"]: row for row in response.context["team_rows"]}
        self.assertIn("Talha Akbar", rows)
        self.assertEqual(rows["Talha Akbar"]["leads"], 1)
        self.assertEqual(rows["Talha Akbar"]["opportunities"], 1)
        self.assertEqual(rows["Talha Akbar"]["production"], 1)
        self.assertEqual(rows["Talha Akbar"]["ready_to_ship"], 1)
        self.assertEqual(rows["Talha Akbar"]["shipped"], 2)
        self.assertEqual(rows["Talha Akbar"]["invoice_revenue"]["CAD"], Decimal("1500"))
        self.assertEqual(rows["Talha Akbar"]["outstanding"]["CAD"], Decimal("750"))

    def test_completed_records_do_not_return_to_active_counts(self):
        metrics = build_sales_kpis(self.talha)
        active_pos = {row["purchase_order_number"] for row in metrics["owner_tables"]["production_orders"]}
        ready_pos = {row["purchase_order_number"] for row in metrics["owner_tables"]["ready_to_ship_orders"]}
        self.assertIn(self.active_order.purchase_order_number, active_pos)
        self.assertIn(self.ready_order.purchase_order_number, ready_pos)
        self.assertNotIn(self.completed_order.purchase_order_number, active_pos)

    def test_dashboard_metrics_are_read_only_and_bounded(self):
        with CaptureQueriesContext(connection) as queries:
            build_sales_kpis(self.talha)
        write_sql = [
            query["sql"]
            for query in queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP"))
        ]
        self.assertEqual(write_sql, [])
        self.assertLessEqual(len(queries), 10)


class SalesKPIArchitectureTests(SimpleTestCase):
    def test_dashboard_adapters_do_not_calculate_kpis(self):
        from crm.services import ceo_executive, sales_attribution, sales_profiles
        from crm import views_people

        profile_source = inspect.getsource(sales_profiles)
        ceo_source = inspect.getsource(ceo_executive)
        people_source = inspect.getsource(views_people)

        self.assertIs(sales_profiles.build_salesperson_profile, sales_attribution.build_sales_kpis)
        self.assertIs(sales_profiles.build_team_performance, sales_attribution.build_team_sales_kpis)
        for forbidden in (".objects", "Sum(", "Count(", "with_pipeline_value", "Closed Won"):
            self.assertNotIn(forbidden, profile_source)
        for forbidden in ("attribution_for", "_ranked_invoice_salespeople", "summarize_pipeline"):
            self.assertNotIn(forbidden, ceo_source)
        self.assertIn("build_ceo_sales_kpis(today)", ceo_source)
        self.assertIn("build_sales_kpis(target_user)", people_source)
        self.assertIn("build_team_sales_kpis(request.GET)", people_source)
