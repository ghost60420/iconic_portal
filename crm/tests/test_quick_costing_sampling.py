from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    ExchangeRate,
    FactoryRunningCostDefault,
    FinanceOperation,
    JournalEntry,
    QuickCosting,
    QuickCostingTimelineSnapshot,
)
from crm.forms_costing import QuickCostingForm
from crm.services.factory_timeline import apply_factory_timeline_to_summary


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, SECURE_SSL_REDIRECT=False)
class QuickCostingSamplingTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="sample-costing-admin",
            email="sample-costing@example.com",
            password="test-pass",
        )
        self.factory_default = FactoryRunningCostDefault.objects.create(
            name="Bangladesh daily factory operating cost",
            side="BD",
            currency="BDT",
            daily_amount=Decimal("10000"),
            effective_from=date(2026, 1, 1),
            created_by=self.admin,
        )
        ExchangeRate.objects.create(cad_to_bdt=Decimal("87"))
        self.client.force_login(self.admin)

    def sample_payload(self, **overrides):
        payload = {
            "costing_type": "quick",
            "sample_mode": "simplified",
            "buyer_name": "Sample Client",
            "project_name": "Two Piece Sample",
            "product_type": "Other",
            "costing_purpose": QuickCosting.PURPOSE_SAMPLE,
            "pricing_type": QuickCosting.PRICING_FULL_PACKAGE,
            "quantity": "2",
            "currency": "CAD",
            "sample_charge": "350.00",
            "shipping_cost": "75.00",
            "other_expenses": "25.00",
            "estimated_production_days": "2",
            "daily_factory_cost": "10000.00",
            "sample_fabric_cost": "0.00",
            "sample_trim_cost": "0.00",
            "sample_print_embroidery_cost": "0.00",
            "sample_wash_cost": "0.00",
            "sample_packaging_cost": "0.00",
            "sample_development_cost": "0.00",
            "target_margin_percent": "",
        }
        payload.update(overrides)
        return payload

    def test_exact_sample_create_refresh_approval_and_no_financial_duplicates(self):
        response = self.client.post(
            reverse("cost_sheet_create"),
            self.sample_payload(),
        )

        self.assertEqual(
            response.status_code,
            302,
            response.context["quick_form"].errors.as_json() if response.context else response.content.decode(),
        )
        quick = QuickCosting.objects.get(project_name="Two Piece Sample")
        self.assertRedirects(response, reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(quick.exchange_rate_bdt_per_cad, Decimal("87.0000"))
        self.assertEqual(QuickCosting.objects.filter(pk=quick.pk).count(), 1)
        self.assertEqual(QuickCostingTimelineSnapshot.objects.filter(quick_costing=quick).count(), 1)
        self.assertEqual(FinanceOperation.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.count(), 0)

        snapshot = quick.factory_timeline
        self.assertEqual(snapshot.estimated_production_days, 2)
        self.assertEqual(snapshot.daily_factory_cost, Decimal("10000.00"))
        self.assertEqual(snapshot.estimated_timeline_cost, Decimal("20000.00"))
        adjusted = apply_factory_timeline_to_summary(
            quick,
            quick.calculation_summary(),
            snapshot=snapshot,
        )
        self.assertEqual(adjusted["factory_timeline_cost"], Decimal("229.89"))
        self.assertEqual(adjusted["total_cost"], Decimal("329.89"))
        self.assertEqual(adjusted["net_profit_total"], Decimal("20.11"))
        self.assertAlmostEqual(adjusted["net_profit_margin_percent"], Decimal("5.7457"), places=4)

        for _ in range(2):
            detail = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
            self.assertEqual(detail.status_code, 200)
            self.assertContains(detail, "Sample Profit Summary")
            self.assertContains(detail, "CAD $350.00")
            self.assertContains(detail, "BDT 20000.00 / CAD $229.89")
            self.assertContains(detail, "CAD $329.89")
            self.assertContains(detail, "CAD $20.11")
            self.assertContains(detail, "Estimated Sample Profit")
            self.assertContains(detail, "Actual Sample Profit")

        submit = self.client.post(reverse("quick_costing_submit_for_approval", args=[quick.pk]))
        self.assertRedirects(submit, reverse("quick_costing_detail", args=[quick.pk]))
        approve = self.client.post(reverse("quick_costing_approve", args=[quick.pk]))
        self.assertRedirects(approve, reverse("quick_costing_detail", args=[quick.pk]))
        quick.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(quick.status, QuickCosting.STATUS_APPROVED)
        self.assertEqual(quick.approved_by, self.admin)
        self.assertIsNotNone(snapshot.locked_at)
        self.assertEqual(QuickCosting.objects.filter(pk=quick.pk).count(), 1)
        self.assertEqual(QuickCostingTimelineSnapshot.objects.filter(quick_costing=quick).count(), 1)
        self.assertEqual(FinanceOperation.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_locked_historical_sample_without_fx_renders_instead_of_500(self):
        quick = QuickCosting.objects.create(
            buyer_name="Historical Sample Client",
            project_name="Historical Sample",
            product_type="Activewear",
            costing_purpose=QuickCosting.PURPOSE_SAMPLE,
            pricing_type=QuickCosting.PRICING_FULL_PACKAGE,
            quantity=6,
            currency="CAD",
            exchange_rate_bdt_per_cad=None,
            shipping_cost=Decimal("150"),
            other_expenses=Decimal("5"),
            selling_price_per_piece=Decimal("64"),
            status=QuickCosting.STATUS_APPROVED,
            created_by=self.admin,
            approved_by=self.admin,
            approved_at=timezone.now(),
        )
        QuickCostingTimelineSnapshot.objects.create(
            quick_costing=quick,
            pricing_type="SAMPLE",
            estimated_production_days=4,
            daily_factory_cost=Decimal("5000"),
            daily_cost_currency="BDT",
            estimated_timeline_cost=Decimal("20000"),
            estimated_profit=Decimal("-210.89"),
            source_default=self.factory_default,
            locked_at=timezone.now(),
            created_by=self.admin,
            modified_by=self.admin,
        )

        response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historical Sample")
        self.assertContains(response, "Sample Profit Summary")
        self.assertContains(response, "CAD $-210.89")
        self.assertNotContains(response, "2. Material Cost")
        self.assertNotContains(response, "requires an approved stored exchange rate")

    def test_sample_pricing_type_also_enables_simplified_sampling(self):
        response = self.client.post(
            reverse("cost_sheet_create"),
            self.sample_payload(
                project_name="Pricing Type Sample",
                costing_purpose=QuickCosting.PURPOSE_BULK,
                pricing_type=QuickCosting.PRICING_SAMPLE,
            ),
        )

        self.assertEqual(response.status_code, 302)
        quick = QuickCosting.objects.get(project_name="Pricing Type Sample")
        self.assertTrue(quick.is_sampling)
        self.assertTrue(quick.uses_simplified_sample_costing)
        self.assertEqual(quick.factory_timeline.pricing_type, "SAMPLE")

    def test_bulk_calculation_contract_is_unchanged(self):
        quick = QuickCosting.objects.create(
            buyer_name="Bulk Client",
            project_name="Bulk Order",
            product_type="Other",
            costing_purpose=QuickCosting.PURPOSE_BULK,
            pricing_type=QuickCosting.PRICING_FOB,
            quantity=100,
            currency="BDT",
            material_cost=Decimal("10000"),
            production_cost=Decimal("20000"),
            other_expenses=Decimal("5000"),
            shipping_cost=Decimal("5000"),
            selling_price_per_piece=Decimal("1000"),
        )

        summary = quick.calculation_summary()

        self.assertEqual(summary["total_cost"], Decimal("40000"))
        self.assertEqual(summary["revenue"], Decimal("100000"))
        self.assertEqual(summary["net_profit_total"], Decimal("60000"))
        self.assertFalse(quick.uses_simplified_sample_costing)

    def test_user_without_override_authority_uses_finance_daily_rate(self):
        payload = self.sample_payload(daily_factory_cost="99999.00")
        form = QuickCostingForm(
            payload,
            factory_default=self.factory_default,
            can_override_factory_rate=False,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["daily_factory_cost"], Decimal("10000.00"))
