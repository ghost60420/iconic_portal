from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    FactoryRunningCostDefault,
    JournalEntry,
    Opportunity,
    ProductionOrder,
    QuickCosting,
)
from crm.services.costing_workflow import CostingWorkflowError, approve_quick_costing
from crm.services.factory_timeline import (
    apply_factory_timeline_to_summary,
    current_estimated_inputs,
    lock_factory_timeline_for_approval,
    record_actual_factory_timeline,
    save_estimated_factory_timeline,
)
from crm.services.financial_reporting import profit_and_loss
from crm.services.production_orders import _quick_costing_approved_summary
from crm.services.production_profit import _local_cost


@override_settings(
    FINANCIAL_CORE_WRITES_ENABLED=True,
    FINANCIAL_CORE_REPORTING_ACTIVE=True,
    SECURE_SSL_REDIRECT=False,
)
class QuickCostingFactoryTimelineTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="timeline-admin",
            email="timeline@example.com",
            password="test-pass",
        )
        self.default = FactoryRunningCostDefault.objects.create(
            name="Approved factory daily operating cost",
            side="BD",
            currency="BDT",
            daily_amount=Decimal("10000"),
            effective_from=date(2026, 1, 1),
            created_by=self.admin,
        )

    def quick_costing(self, **overrides):
        values = {
            "buyer_name": "Timeline Buyer",
            "project_name": "Timeline Order",
            "product_type": "T-Shirt",
            "costing_purpose": QuickCosting.PURPOSE_BULK,
            "pricing_type": QuickCosting.PRICING_FULL_PACKAGE,
            "quantity": 100,
            "currency": "BDT",
            "material_cost": Decimal("10000"),
            "production_cost": Decimal("20000"),
            "other_expenses": Decimal("5000"),
            "shipping_cost": Decimal("5000"),
            "selling_price_per_piece": Decimal("1000"),
            "commission_type": QuickCosting.COMMISSION_NONE,
            "target_margin_percent": Decimal("20"),
            "created_by": self.admin,
        }
        values.update(overrides)
        return QuickCosting.objects.create(**values)

    def save_estimate(self, quick, *, days=5, daily_amount=None):
        return save_estimated_factory_timeline(
            quick,
            estimated_days=days,
            daily_default=self.default,
            daily_amount_snapshot=daily_amount,
            actor=self.admin,
            **current_estimated_inputs(quick),
        )

    def test_five_days_at_ten_thousand_and_profit_include_timeline_once(self):
        quick = self.quick_costing()
        snapshot = self.save_estimate(quick)
        adjusted = apply_factory_timeline_to_summary(quick, quick.calculation_summary(), snapshot=snapshot)

        self.assertEqual(snapshot.estimated_timeline_cost, Decimal("50000.00"))
        self.assertEqual(snapshot.estimated_profit, Decimal("10000.00"))
        self.assertEqual(adjusted["total_cost"], Decimal("90000.00"))
        self.assertEqual(adjusted["net_profit_total"], Decimal("10000.00"))
        self.assertEqual(adjusted["factory_timeline_cost"], Decimal("50000.00"))

    def test_daily_rate_snapshot_does_not_change_with_finance_default(self):
        quick = self.quick_costing()
        snapshot = self.save_estimate(quick)
        quick.status = QuickCosting.STATUS_APPROVED
        quick.approved_by = self.admin
        quick.approved_at = timezone.now()
        quick.save(update_fields=("status", "approved_by", "approved_at", "updated_at"))
        snapshot = lock_factory_timeline_for_approval(quick, actor=self.admin)

        self.default.daily_amount = Decimal("20000")
        self.default.save(update_fields=("daily_amount", "modified_at"))
        snapshot.refresh_from_db()

        self.assertEqual(snapshot.daily_factory_cost, Decimal("10000.00"))
        self.assertEqual(snapshot.estimated_timeline_cost, Decimal("50000.00"))

    def test_actual_days_cost_variances_and_not_available_profit(self):
        quick = self.quick_costing()
        snapshot = self.save_estimate(quick)
        quick.status = QuickCosting.STATUS_APPROVED
        quick.approved_by = self.admin
        quick.approved_at = timezone.now()
        quick.save(update_fields=("status", "approved_by", "approved_at", "updated_at"))
        snapshot = lock_factory_timeline_for_approval(quick, actor=self.admin)

        snapshot = record_actual_factory_timeline(snapshot, actual_days=7, actor=self.admin)

        self.assertEqual(snapshot.actual_timeline_cost, Decimal("70000.00"))
        self.assertIsNone(snapshot.actual_profit)
        self.assertEqual(snapshot.timeline_variance, 2)
        self.assertIsNone(snapshot.profit_variance)
        self.assertEqual(snapshot.status, snapshot.STATUS_RED)

        snapshot = record_actual_factory_timeline(
            snapshot,
            actual_days=7,
            actual_revenue=Decimal("90000"),
            other_actual_cost=Decimal("30000"),
            actor=self.admin,
        )
        self.assertEqual(snapshot.actual_profit, Decimal("-10000.00"))
        self.assertEqual(snapshot.profit_variance, Decimal("-20000.00"))

    def test_page_places_live_section_between_production_and_sales(self):
        quick = self.quick_costing()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("3. Production Cost"), html.index("4. Production Timeline and Factory Cost"))
        self.assertLess(html.index("4. Production Timeline and Factory Cost"), html.index("5. Sales"))
        self.assertContains(response, "6. Profit Summary")
        self.assertContains(response, "7. Live Order Summary")
        self.assertContains(response, "Estimated Production Days")
        self.assertContains(response, "Actual Profit")
        self.assertContains(response, "Not Available")

    def test_sales_can_enter_days_but_cannot_override_finance_rate(self):
        sales = get_user_model().objects.create_user(username="timeline-sales", password="test-pass")
        Group.objects.get_or_create(name="Sales")[0].user_set.add(sales)
        sales.access.can_costing = True
        sales.access.can_view_internal_costing = True
        sales.access.save()
        quick = self.quick_costing(created_by=sales)
        self.client.force_login(sales)

        response = self.client.post(
            reverse("financial_quick_costing_timeline", args=[quick.pk]),
            {
                "action": "estimate",
                "estimate-estimated_days": "5",
                "estimate-daily_factory_cost": "1",
                "estimate-daily_cost_currency": "CAD",
            },
        )

        self.assertRedirects(response, reverse("quick_costing_detail", args=[quick.pk]))
        snapshot = quick.factory_timeline
        self.assertEqual(snapshot.daily_factory_cost, Decimal("10000.00"))
        self.assertEqual(snapshot.daily_cost_currency, "BDT")

    def test_approval_requires_timeline_when_finance_default_applies(self):
        quick = self.quick_costing(status=QuickCosting.STATUS_SUBMITTED)
        quick.approval_submitted_by = self.admin
        quick.approval_submitted_at = timezone.now()
        quick.save(update_fields=("approval_submitted_by", "approval_submitted_at", "updated_at"))

        with self.assertRaisesMessage(CostingWorkflowError, "Estimated Production Days"):
            approve_quick_costing(quick, approver=self.admin)

        self.save_estimate(quick)
        approved, _order, _created = approve_quick_costing(quick, approver=self.admin)
        self.assertEqual(approved.status, QuickCosting.STATUS_APPROVED)
        approved.factory_timeline.refresh_from_db()
        self.assertIsNotNone(approved.factory_timeline.locked_at)

    def test_cad_and_usd_profit_use_stored_quick_costing_exchange_rates(self):
        cad = self.quick_costing(
            currency="CAD",
            exchange_rate_bdt_per_cad=Decimal("100"),
            material_cost=Decimal("100"),
            production_cost=Decimal("100"),
            other_expenses=Decimal("50"),
            shipping_cost=Decimal("50"),
            selling_price_per_piece=Decimal("10"),
        )
        cad_snapshot = self.save_estimate(cad)
        self.assertEqual(cad_snapshot.estimated_profit, Decimal("200.00"))

        opportunity = Opportunity.objects.create(
            product_category="Other",
            product_type="Other",
            fx_rate_bdt_per_usd=Decimal("120"),
        )
        usd = self.quick_costing(
            opportunity=opportunity,
            currency="USD",
            material_cost=Decimal("100"),
            production_cost=Decimal("100"),
            other_expenses=Decimal("50"),
            shipping_cost=Decimal("50"),
            selling_price_per_piece=Decimal("10"),
        )
        usd_snapshot = self.save_estimate(usd)
        self.assertEqual(usd_snapshot.estimated_profit, Decimal("283.33"))

    def test_all_pricing_types_share_the_same_timeline_formula(self):
        pricing_rows = (
            (QuickCosting.PRICING_FULL_PACKAGE, QuickCosting.PURPOSE_BULK, "FULL_PACKAGE"),
            (QuickCosting.PRICING_FOB, QuickCosting.PURPOSE_BULK, "FOB"),
            (QuickCosting.PRICING_CMT, QuickCosting.PURPOSE_BULK, "CMT"),
            ("door_to_door", QuickCosting.PURPOSE_BULK, "DOOR_TO_DOOR"),
            (QuickCosting.PRICING_FULL_PACKAGE, QuickCosting.PURPOSE_SAMPLE, "SAMPLE"),
            ("other", QuickCosting.PURPOSE_BULK, "OTHER"),
        )
        for index, (pricing_type, purpose, expected_snapshot_type) in enumerate(pricing_rows):
            with self.subTest(pricing_type=pricing_type, purpose=purpose):
                quick = self.quick_costing(
                    project_name=f"Pricing {index}",
                    pricing_type=pricing_type,
                    costing_purpose=purpose,
                )
                snapshot = self.save_estimate(quick)
                self.assertEqual(snapshot.pricing_type, expected_snapshot_type)
                self.assertEqual(snapshot.estimated_timeline_cost, Decimal("50000.00"))

    def test_production_handoff_carries_snapshot_and_actual_overhead_wins(self):
        quick = self.quick_costing()
        snapshot = self.save_estimate(quick)
        summary = apply_factory_timeline_to_summary(quick, quick.calculation_summary(), snapshot=snapshot)
        approved = _quick_costing_approved_summary(quick, summary, invoice=None)
        self.assertEqual(approved["estimated_production_days"], 5)
        self.assertEqual(approved["daily_factory_cost"], "10000.00")
        self.assertEqual(approved["estimated_factory_timeline_cost"], "50000.00")
        self.assertEqual(approved["estimated_profit"], "10000.00")

        local_quick = self.quick_costing(
            project_name="Local Timeline Order",
            pricing_type=QuickCosting.PRICING_CMT,
            sewing_charge_per_piece_bdt=Decimal("1000"),
            sewing_cost_per_piece_bdt=Decimal("100"),
            extra_local_cost_bdt=Decimal("0"),
        )
        self.save_estimate(local_quick)
        local_quick.status = QuickCosting.STATUS_APPROVED
        local_quick.approved_by = self.admin
        local_quick.approved_at = timezone.now()
        local_quick.save(update_fields=("status", "approved_by", "approved_at", "updated_at"))
        order = ProductionOrder.objects.create(
            title="Timeline Production",
            factory_location="bd",
            order_type="sewing_charge",
            qty_total=100,
            sewing_cost_per_piece_bdt=Decimal("100"),
            source_quick_costing=local_quick,
        )
        estimated_cost, estimated_source = _local_cost(order, [])
        self.assertEqual(estimated_cost, Decimal("60000.00"))
        self.assertIn("Estimated factory timeline", estimated_source)

        actual_cost, actual_source = _local_cost(order, [{
            "side": "BD",
            "direction": "OUT",
            "main_type": "EXPENSE",
            "sub_type": "Factory daily overhead",
            "description": "Factory running cost",
            "internal_note": "",
            "amount_bdt": Decimal("40000"),
            "currency": "BDT",
            "amount_original": Decimal("40000"),
        }])
        self.assertEqual(actual_cost, Decimal("50000.00"))
        self.assertIn("Posted factory overhead", actual_source)

    def test_timeline_snapshot_never_posts_a_financial_journal(self):
        quick = self.quick_costing()
        before = JournalEntry.objects.count()
        report_before = profit_and_loss(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.save_estimate(quick)
        self.assertEqual(JournalEntry.objects.count(), before)
        report_after = profit_and_loss(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.assertEqual(report_after, report_before)
