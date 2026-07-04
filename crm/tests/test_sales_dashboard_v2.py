import inspect
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

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
        self.assertEqual(pipeline, {"CAD": Decimal("1200"), "USD": Decimal("0"), "BDT": Decimal("0")})
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
        self.assertIn("build_team_sales_kpis()", people_source)
