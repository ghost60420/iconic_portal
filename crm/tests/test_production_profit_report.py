from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    ExchangeRate,
    Invoice,
    ProductionOrder,
    QuickCosting,
)
from crm.services.production_profit import build_production_profit_report


class ProductionProfitReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="production-profit-admin",
            email="production-profit@example.com",
            password="pass",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def report(self, **kwargs):
        return build_production_profit_report(
            year=self.today.year,
            month=self.today.month,
            **kwargs,
        )

    def canada_order(self, *, code, cost_bdt=None, sewing_cost_bdt=None):
        return ProductionOrder.objects.create(
            title=f"Canada order {code}",
            order_code=code,
            order_type="fob",
            factory_location="bd",
            qty_total=100,
            production_total_cost_bdt=cost_bdt,
            production_sewing_cost_bdt=sewing_cost_bdt,
        )

    def local_order(self, *, code, charge=None, cost=None, extra=Decimal("0")):
        quick = QuickCosting.objects.create(
            buyer_name=f"Local buyer {code}",
            project_name=f"Local project {code}",
            product_type="Other",
            pricing_type=QuickCosting.PRICING_CMT,
            currency="BDT",
            quantity=100,
            sewing_charge_per_piece_bdt=charge,
            sewing_cost_per_piece_bdt=cost,
            extra_local_cost_bdt=extra,
            status=QuickCosting.STATUS_APPROVED,
            approved_at=timezone.now(),
        )
        return ProductionOrder.objects.create(
            title=f"Local order {code}",
            order_code=code,
            order_type="sewing_charge",
            factory_location="bd",
            qty_total=100,
            sewing_charge_per_piece_bdt=charge,
            sewing_cost_per_piece_bdt=cost,
            extra_local_cost_bdt=extra,
            source_quick_costing=quick,
        )

    def invoice(
        self,
        order,
        *,
        number,
        amount,
        currency,
        market,
        region,
        invoice_type="bulk",
    ):
        values = {
            "invoice_number": number,
            "order": order,
            "issue_date": self.today,
            "currency": currency,
            "invoice_market": market,
            "invoice_region": region,
            "invoice_type": invoice_type,
            "subtotal": amount,
            "total_amount": amount,
        }
        if order.source_quick_costing_id:
            values["quick_costing"] = order.source_quick_costing
        return Invoice.objects.create(**values)

    def test_canada_invoice_revenue_and_bdt_cost_convert_to_cad(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        order = self.canada_order(code="POCANADA001", cost_bdt=Decimal("85000"))
        self.invoice(
            order,
            number="INV-PROFIT-CA",
            amount=Decimal("2500"),
            currency="CAD",
            market="north_america",
            region="CA",
        )

        report = self.report()
        row = next(item for item in report["rows"] if item["production_order_id"] == order.pk)

        self.assertEqual(row["classification"], "canada_export")
        self.assertEqual(row["revenue_amount"], Decimal("2500.00"))
        self.assertEqual(row["cost_bdt"], Decimal("85000.00"))
        self.assertEqual(row["cost_cad"], Decimal("1000.00"))
        self.assertEqual(row["profit"], Decimal("1500.00"))
        self.assertEqual(row["margin_pct"], Decimal("60.00"))
        self.assertEqual(report["canada_export"]["profit"], Decimal("1500.00"))

    def test_bangladesh_local_revenue_cost_profit_and_margin_are_bdt(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        order = self.local_order(
            code="POLOCAL001",
            charge=Decimal("120"),
            cost=Decimal("70"),
            extra=Decimal("500"),
        )
        self.invoice(
            order,
            number="INV-PROFIT-BD",
            amount=Decimal("12000"),
            currency="BDT",
            market="bangladesh",
            region="BD",
            invoice_type="sewing_charge",
        )

        report = self.report()
        row = next(item for item in report["rows"] if item["production_order_id"] == order.pk)

        self.assertEqual(row["classification"], "bangladesh_local")
        self.assertEqual(row["revenue_currency"], "BDT")
        self.assertEqual(row["revenue_amount"], Decimal("12000.00"))
        self.assertEqual(row["cost_bdt"], Decimal("7500.00"))
        self.assertEqual(row["profit"], Decimal("4500.00"))
        self.assertEqual(row["margin_pct"], Decimal("37.50"))
        self.assertEqual(report["bangladesh_local_sewing"]["profit"], Decimal("4500.00"))

    def test_canada_sewing_charge_invoice_uses_sewing_cost_only(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        order = self.canada_order(
            code="POSEWINGCA001",
            cost_bdt=Decimal("85000"),
            sewing_cost_bdt=Decimal("42500"),
        )
        self.invoice(
            order,
            number="INV-PROFIT-SEW-CA",
            amount=Decimal("1000"),
            currency="CAD",
            market="north_america",
            region="CA",
            invoice_type="sewing_charge",
        )

        summary = self.report()["canada_export_sewing"]

        self.assertEqual(summary["revenue"], Decimal("1000.00"))
        self.assertEqual(summary["cost_bdt"], Decimal("42500.00"))
        self.assertEqual(summary["cost_cad"], Decimal("500.00"))
        self.assertEqual(summary["profit"], Decimal("500.00"))
        self.assertEqual(summary["margin_pct"], Decimal("50.00"))

    def test_canada_sewing_subtype_uses_linked_accounting_revenue(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        order = self.canada_order(
            code="POSEWINGSUBTYPE",
            cost_bdt=Decimal("85000"),
            sewing_cost_bdt=Decimal("17000"),
        )
        self.invoice(
            order,
            number="INV-PROFIT-BULK-WITH-SEWING",
            amount=Decimal("1000"),
            currency="CAD",
            market="north_america",
            region="CA",
            invoice_type="bulk",
        )
        AccountingEntry.objects.create(
            date=self.today,
            side="CA",
            direction="IN",
            main_type="REVENUE",
            sub_type="Swing",
            production_order=order,
            currency="CAD",
            amount_original=Decimal("400"),
            rate_to_bdt=Decimal("85"),
        )

        report = self.report()
        row = next(item for item in report["rows"] if item["production_order_id"] == order.pk)
        summary = report["canada_export_sewing"]

        self.assertEqual(row["revenue_amount"], Decimal("1000.00"))
        self.assertEqual(row["sewing_charge_amount"], Decimal("400.00"))
        self.assertEqual(row["sewing_charge_source"], "CA accounting sewing revenue")
        self.assertEqual(summary["revenue"], Decimal("400.00"))
        self.assertEqual(summary["cost_cad"], Decimal("200.00"))
        self.assertEqual(summary["profit"], Decimal("200.00"))
        self.assertEqual(summary["margin_pct"], Decimal("50.00"))

    def test_missing_cost_and_missing_revenue_never_guess_profit(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        missing_cost = self.canada_order(code="POMISSINGCOST")
        self.invoice(
            missing_cost,
            number="INV-PROFIT-NO-COST",
            amount=Decimal("1000"),
            currency="CAD",
            market="north_america",
            region="CA",
        )
        missing_revenue = self.canada_order(
            code="POMISSINGREVENUE",
            cost_bdt=Decimal("8500"),
        )

        rows = {row["production_order_id"]: row for row in self.report()["rows"]}

        self.assertEqual(rows[missing_cost.pk]["data_status"], "Missing cost")
        self.assertIsNone(rows[missing_cost.pk]["profit"])
        self.assertIsNone(rows[missing_cost.pk]["margin_pct"])
        self.assertEqual(rows[missing_revenue.pk]["data_status"], "Missing revenue")
        self.assertIsNone(rows[missing_revenue.pk]["profit"])
        self.assertIsNone(rows[missing_revenue.pk]["margin_pct"])

    def test_missing_exchange_rate_keeps_canada_profit_unavailable(self):
        order = self.canada_order(code="POMISSINGRATE", cost_bdt=Decimal("8500"))
        self.invoice(
            order,
            number="INV-PROFIT-NO-RATE",
            amount=Decimal("200"),
            currency="CAD",
            market="north_america",
            region="CA",
        )

        row = next(
            item for item in self.report()["rows"]
            if item["production_order_id"] == order.pk
        )

        self.assertEqual(row["data_status"], "Missing exchange rate")
        self.assertIsNone(row["cost_cad"])
        self.assertIsNone(row["profit"])
        self.assertIsNone(row["margin_pct"])

    def test_combined_view_converts_local_revenue_and_never_adds_bdt_as_cad(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        canada = self.canada_order(code="POCOMBINEDCA", cost_bdt=Decimal("85000"))
        self.invoice(
            canada,
            number="INV-PROFIT-COMBINED-CA",
            amount=Decimal("2000"),
            currency="CAD",
            market="north_america",
            region="CA",
        )
        local = self.local_order(
            code="POCOMBINEDBD",
            charge=Decimal("850"),
            cost=Decimal("425"),
        )
        self.invoice(
            local,
            number="INV-PROFIT-COMBINED-BD",
            amount=Decimal("85000"),
            currency="BDT",
            market="bangladesh",
            region="BD",
            invoice_type="sewing_charge",
        )

        combined = self.report()["combined"]

        self.assertTrue(combined["complete"])
        self.assertEqual(combined["revenue_cad"], Decimal("3000.00"))
        self.assertEqual(combined["cost_cad"], Decimal("1500.00"))
        self.assertEqual(combined["profit_cad"], Decimal("1500.00"))
        self.assertEqual(combined["margin_pct"], Decimal("50.00"))

    def test_mixed_invoice_currencies_are_unclassified_and_not_combined(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        order = self.canada_order(code="POMIXED001", cost_bdt=Decimal("8500"))
        self.invoice(
            order,
            number="INV-PROFIT-MIX-CAD",
            amount=Decimal("100"),
            currency="CAD",
            market="north_america",
            region="CA",
        )
        self.invoice(
            order,
            number="INV-PROFIT-MIX-BDT",
            amount=Decimal("8500"),
            currency="BDT",
            market="bangladesh",
            region="BD",
        )

        report = self.report()
        row = next(item for item in report["rows"] if item["production_order_id"] == order.pk)

        self.assertEqual(row["classification"], "unclassified")
        self.assertEqual(row["data_status"], "Unavailable")
        self.assertIsNone(row["profit"])
        self.assertFalse(report["combined"]["complete"])
        self.assertIsNone(report["combined"]["revenue_cad"])

    def test_friendly_po_renders_and_internal_id_search_finds_order(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        order = self.canada_order(code="PO260706120000ABC123", cost_bdt=Decimal("8500"))
        self.invoice(
            order,
            number="INV-PROFIT-SEARCH",
            amount=Decimal("200"),
            currency="CAD",
            market="north_america",
            region="CA",
        )

        response = self.client.get(
            reverse("production_profit_report"),
            {
                "year": self.today.year,
                "month": self.today.month,
                "q": order.internal_order_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.purchase_order_number)
        self.assertContains(response, "Internal Order ID")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["production_order_id"], order.pk)

    def test_render_is_read_only_and_does_not_create_exchange_rate(self):
        self.canada_order(code="POREADONLY", cost_bdt=Decimal("8500"))
        models = (ProductionOrder, Invoice, AccountingEntry, QuickCosting, ExchangeRate)
        before = {model: model.objects.count() for model in models}

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                reverse("production_profit_report"),
                {"year": self.today.year, "month": self.today.month},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exchange rate unavailable")
        self.assertEqual({model: model.objects.count() for model in models}, before)
        write_sql = [
            query["sql"]
            for query in captured
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(write_sql, [])

    def test_report_query_count_is_bounded_and_has_no_n_plus_one(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85"))
        first = self.canada_order(code="POQUERY001", cost_bdt=Decimal("8500"))
        self.invoice(
            first,
            number="INV-PROFIT-QUERY-1",
            amount=Decimal("200"),
            currency="CAD",
            market="north_america",
            region="CA",
        )
        with CaptureQueriesContext(connection) as one_order_queries:
            self.report()

        for index in range(2, 7):
            order = self.canada_order(
                code=f"POQUERY{index:03d}",
                cost_bdt=Decimal("8500"),
            )
            self.invoice(
                order,
                number=f"INV-PROFIT-QUERY-{index}",
                amount=Decimal("200"),
                currency="CAD",
                market="north_america",
                region="CA",
            )

        with CaptureQueriesContext(connection) as six_order_queries:
            self.report()

        self.assertLessEqual(len(one_order_queries), 5)
        self.assertEqual(len(six_order_queries), len(one_order_queries))
