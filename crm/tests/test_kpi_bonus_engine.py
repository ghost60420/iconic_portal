from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from crm.models_kpi_bonus import (
    KPIBonusCalculation,
    KPIBonusRuleSet,
    KPIBonusWeightProfile,
)
from crm.models_kpi_reviews import KPIReview
from crm.services.kpi_assignments import update_assignment
from crm.services.kpi_bonus import (
    BLOCKED,
    BONUS_CALCULATION_VERSION,
    BONUS_FORMULA_VERSION,
    ELIGIBLE,
    NOT_ELIGIBLE,
    PENDING,
    KPIBonusError,
    create_bonus_calculation,
    effective_bonus_rule_set,
    evaluate_bonus,
    verify_bonus_calculation,
)
from crm.services.kpi_reviews import (
    approve_kpi_review,
    create_kpi_review,
    reject_kpi_review,
    save_kpi_review_draft,
    start_kpi_review,
    submit_kpi_review,
)
from crm.tests.test_kpi_performance_ui import KPIReviewTestBase


class KPIBonusEngineTests(KPIReviewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.weight_profile = cls.create_weight_profile(
            code="standard-incentive",
            version=1,
            individual=Decimal("60.00"),
            team=Decimal("25.00"),
            company=Decimal("15.00"),
            end=date(2026, 12, 31),
        )
        cls.rule_set = cls.create_rule_set(
            code="standard-incentive",
            version=1,
            weight_profile=cls.weight_profile,
            end=date(2026, 12, 31),
        )

    @classmethod
    def create_weight_profile(
        cls,
        *,
        code,
        version,
        individual,
        team,
        company,
        start=date(2026, 1, 1),
        end=None,
    ):
        profile = KPIBonusWeightProfile.objects.create(
            code=code,
            name=f"{code.replace('-', ' ').title()} Weights",
            version=version,
            team_scope_code="regional-sales",
            team_scope_name="Regional Sales",
            individual_weight=individual,
            team_weight=team,
            company_weight=company,
            effective_start=start,
            effective_end=end,
            created_by=cls.admin,
        )
        profile.status = KPIBonusWeightProfile.STATUS_PUBLISHED
        profile.published_by = cls.admin
        profile.save()
        return profile

    @classmethod
    def create_rule_set(
        cls,
        *,
        code,
        version,
        weight_profile,
        start=date(2026, 1, 1),
        end=None,
        minimum_score=Decimal("70.00"),
        floor=Decimal("100.00"),
        cap=Decimal("800.00"),
        critical=KPIBonusRuleSet.CRITICAL_BLOCK,
        enabled=True,
    ):
        rule = KPIBonusRuleSet.objects.create(
            code=code,
            name=f"{code.replace('-', ' ').title()} Rules",
            version=version,
            weight_profile=weight_profile,
            bonus_enabled=enabled,
            minimum_score=minimum_score,
            attendance_multiplier_default=Decimal("1.0000"),
            attendance_multiplier_min=Decimal("0.0000"),
            attendance_multiplier_max=Decimal("1.2000"),
            critical_red_behavior=critical,
            bonus_floor=floor,
            bonus_cap=cap,
            currency="CAD",
            eligible_employee_statuses=["active"],
            approval_required=True,
            effective_start=start,
            effective_end=end,
            created_by=cls.admin,
        )
        rule.status = KPIBonusRuleSet.STATUS_PUBLISHED
        rule.published_by = cls.admin
        rule.save()
        return rule

    def approved_month(self, month, score, *, critical=False):
        start = date(2026, month, 1)
        end = date(2026, month, 28)
        review_date = date(2026, month, 15)
        review = create_kpi_review(
            employee=self.employee,
            period_type=KPIReview.PERIOD_MONTHLY,
            period_start=start,
            period_end=end,
            review_date=review_date,
            actor=self.manager,
        )
        entry = review.item_entries.get()
        save_kpi_review_draft(
            review,
            actor=self.manager,
            entry_values={
                entry.pk: {
                    "actual_value": Decimal(str(score)),
                    "critical_red": critical,
                    "critical_reason": "Critical quality breach" if critical else "",
                    "critical_trigger": "Quality escalation" if critical else "",
                    "item_comment": "Bonus engine source",
                }
            },
        )
        submit_kpi_review(review, actor=self.manager)
        start_kpi_review(review, actor=self.director)
        return approve_kpi_review(review, actor=self.director)

    def individual_only_configuration(self, code="individual-only", **rule_kwargs):
        profile = self.create_weight_profile(
            code=code,
            version=1,
            individual=Decimal("100.00"),
            team=Decimal("0.00"),
            company=Decimal("0.00"),
        )
        rule = self.create_rule_set(
            code=code,
            version=1,
            weight_profile=profile,
            **rule_kwargs,
        )
        return rule

    def test_individual_bonus_uses_approved_snapshot(self):
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration()

        result = evaluate_bonus(
            review,
            rule_set=rule,
            base_bonus_amount=Decimal("1000"),
        )

        self.assertEqual(result.eligibility_status, ELIGIBLE)
        self.assertEqual(result.individual_score, Decimal("90.000000"))
        self.assertEqual(result.final_bonus_score, Decimal("90.000000"))
        self.assertEqual(result.final_bonus_amount, Decimal("800.00"))
        self.assertTrue(result.bonus_cap_applied)

    def test_team_and_company_weighting(self):
        review = self.approved_month(2, Decimal("90"))
        team_review = self.approved_month(3, Decimal("80"))
        company_review = self.approved_month(4, Decimal("100"))

        result = evaluate_bonus(
            review,
            rule_set=self.rule_set,
            team_review=team_review,
            company_review=company_review,
            base_bonus_amount=Decimal("500"),
        )

        self.assertEqual(result.individual_weighted_score, Decimal("54.000000"))
        self.assertEqual(result.team_weighted_score, Decimal("20.000000"))
        self.assertEqual(result.company_weighted_score, Decimal("15.000000"))
        self.assertEqual(result.final_bonus_score, Decimal("89.000000"))
        self.assertEqual(result.final_bonus_amount, Decimal("445.00"))
        self.assertEqual(result.team_source.review_id, team_review.pk)
        self.assertEqual(result.company_source.review_id, company_review.pk)

    def test_critical_red_blocks_without_losing_score_source(self):
        review = self.approved_month(1, Decimal("95"), critical=True)
        rule = self.individual_only_configuration(code="critical-block")

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, BLOCKED)
        self.assertEqual(result.reasons, ("critical_red",))
        self.assertEqual(result.review_source.score, Decimal("95.000000"))
        self.assertTrue(result.critical_red)

    def test_critical_red_behavior_is_configurable(self):
        review = self.approved_month(1, Decimal("95"), critical=True)
        rule = self.individual_only_configuration(
            code="critical-allowed",
            critical=KPIBonusRuleSet.CRITICAL_ALLOW,
        )

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, ELIGIBLE)
        self.assertIn("critical_red_allowed", result.reasons)

    def test_minimum_score_marks_not_eligible(self):
        review = self.approved_month(1, Decimal("69.99"))
        rule = self.individual_only_configuration(code="minimum-score")

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, NOT_ELIGIBLE)
        self.assertEqual(result.reasons, ("insufficient_score",))

    def test_maximum_score_is_bounded_and_cap_applies(self):
        review = self.approved_month(1, Decimal("100"))
        rule = self.individual_only_configuration(code="maximum-score")

        result = evaluate_bonus(
            review,
            rule_set=rule,
            base_bonus_amount=Decimal("1000"),
        )

        self.assertEqual(result.final_bonus_score, Decimal("100.000000"))
        self.assertEqual(result.final_bonus_amount, Decimal("800.00"))
        self.assertTrue(result.bonus_cap_applied)

    def test_bonus_floor_applies_after_score_and_attendance(self):
        review = self.approved_month(1, Decimal("80"))
        rule = self.individual_only_configuration(code="bonus-floor")

        result = evaluate_bonus(
            review,
            rule_set=rule,
            attendance_multiplier=Decimal("0.5000"),
            base_bonus_amount=Decimal("100"),
        )

        self.assertEqual(result.calculated_bonus_amount, Decimal("40.00"))
        self.assertEqual(result.final_bonus_amount, Decimal("100.00"))
        self.assertTrue(result.bonus_floor_applied)

    def test_attendance_multiplier_must_use_configured_range(self):
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration(code="attendance")

        with self.assertRaisesMessage(KPIBonusError, "outside"):
            evaluate_bonus(
                review,
                rule_set=rule,
                attendance_multiplier=Decimal("1.5000"),
            )

    def test_inactive_employee_is_not_eligible(self):
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration(code="inactive-employee")
        self.employee.status = self.employee.STATUS_INACTIVE
        self.employee.save(update_fields=["status"])

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, NOT_ELIGIBLE)
        self.assertEqual(result.reasons, ("employee_inactive",))

    def test_pending_review_is_not_calculated_or_stored(self):
        review = self.create_review()
        rule = self.individual_only_configuration(code="pending-review")

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, PENDING)
        self.assertEqual(result.reasons, ("review_review_not_approved",))
        self.assertEqual(KPIBonusCalculation.objects.count(), 0)
        with self.assertRaisesMessage(KPIBonusError, "Pending"):
            create_bonus_calculation(
                review,
                rule_set=rule,
                base_bonus_amount=Decimal("1000"),
            )

    def test_rejected_review_returns_pending_evaluation(self):
        review = self.create_review()
        self.save_review(review)
        submit_kpi_review(review, actor=self.manager)
        start_kpi_review(review, actor=self.director)
        review = reject_kpi_review(
            review,
            actor=self.director,
            comment="Correction required.",
        )
        rule = self.individual_only_configuration(code="rejected-review")

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(review.status, KPIReview.STATUS_DRAFT)
        self.assertEqual(result.eligibility_status, PENDING)

    def test_non_bonus_assignment_is_not_eligible(self):
        assignment = update_assignment(
            self.assignment,
            actor=self.admin,
            reason="Exclude this role from bonus",
            bonus_eligible=False,
        )
        self.assignment = assignment
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration(code="assignment-ineligible")

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, NOT_ELIGIBLE)
        self.assertEqual(result.reasons, ("bonus_ineligible_assignment",))

    def test_later_assignment_change_does_not_change_historical_eligibility(self):
        review = self.approved_month(1, Decimal("90"))
        update_assignment(
            self.assignment,
            actor=self.admin,
            reason="Future bonus policy change",
            bonus_eligible=False,
        )
        rule = self.individual_only_configuration(code="assignment-history")

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, ELIGIBLE)
        self.assertEqual(result.individual_score, Decimal("90.000000"))

    def test_bonus_disabled_blocks_calculation(self):
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration(
            code="bonus-disabled",
            enabled=False,
        )

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, BLOCKED)
        self.assertEqual(result.reasons, ("bonus_disabled",))

    def test_missing_team_or_company_review_is_pending(self):
        review = self.approved_month(1, Decimal("90"))

        result = evaluate_bonus(review, rule_set=self.rule_set)

        self.assertEqual(result.eligibility_status, PENDING)
        self.assertEqual(
            result.reasons,
            ("team_review_missing", "company_review_missing"),
        )

    def test_invalid_approved_snapshot_is_blocked(self):
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration(code="invalid-snapshot")
        review.snapshot_digest = "0" * 64
        KPIReview._base_manager.filter(pk=review.pk).update(
            snapshot_digest=review.snapshot_digest
        )

        result = evaluate_bonus(review, rule_set=rule)

        self.assertEqual(result.eligibility_status, BLOCKED)
        self.assertEqual(result.reasons, ("review_snapshot_invalid",))

    def test_effective_rule_versions_are_selected_by_review_date(self):
        profile_v2 = self.create_weight_profile(
            code="standard-incentive",
            version=2,
            individual=Decimal("70.00"),
            team=Decimal("20.00"),
            company=Decimal("10.00"),
            start=date(2027, 1, 1),
        )
        rule_v2 = self.create_rule_set(
            code="standard-incentive",
            version=2,
            weight_profile=profile_v2,
            start=date(2027, 1, 1),
        )

        self.assertEqual(
            effective_bonus_rule_set(
                date(2026, 7, 15),
                code="standard-incentive",
            ).pk,
            self.rule_set.pk,
        )
        self.assertEqual(
            effective_bonus_rule_set(
                date(2027, 7, 15),
                code="standard-incentive",
            ).pk,
            rule_v2.pk,
        )

    def test_stored_bonus_snapshot_is_immutable_and_reused(self):
        review = self.approved_month(1, Decimal("90"))
        rule = self.individual_only_configuration(code="stored-history")
        calculation = create_bonus_calculation(
            review,
            rule_set=rule,
            base_bonus_amount=Decimal("1000"),
            actor=self.admin,
        )
        original = calculation.result_snapshot

        self.assertTrue(verify_bonus_calculation(calculation))
        self.assertEqual(calculation.formula_version, BONUS_FORMULA_VERSION)
        self.assertEqual(
            calculation.calculation_version,
            BONUS_CALCULATION_VERSION,
        )
        self.assertEqual(
            calculation.result_snapshot["sources"]["review"]["review_id"],
            review.pk,
        )

        calculation.result_snapshot = {"changed": True}
        with self.assertRaisesMessage(ValidationError, "bonus service"):
            calculation.save()
        calculation.refresh_from_db()
        self.assertEqual(calculation.result_snapshot, original)

        existing = create_bonus_calculation(
            review,
            rule_set=rule,
            base_bonus_amount=Decimal("10"),
            actor=self.admin,
        )
        self.assertEqual(existing.pk, calculation.pk)
        self.assertEqual(existing.result_snapshot, original)

    def test_published_configuration_is_immutable(self):
        self.rule_set.minimum_score = Decimal("60.00")
        with self.assertRaisesMessage(ValidationError, "immutable"):
            self.rule_set.save()

        self.weight_profile.team_scope_name = "Changed"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            self.weight_profile.save()

    def test_bonus_weight_total_is_exact(self):
        with self.assertRaisesMessage(ValidationError, "exactly 100"):
            KPIBonusWeightProfile.objects.create(
                code="invalid-total",
                name="Invalid Total",
                version=1,
                team_scope_code="any-team",
                team_scope_name="Any Team",
                individual_weight=Decimal("50"),
                team_weight=Decimal("20"),
                company_weight=Decimal("20"),
                effective_start=date(2026, 1, 1),
            )

    def test_evaluation_query_count_is_constant_for_three_sources(self):
        review = self.approved_month(2, Decimal("90"))
        team_review = self.approved_month(3, Decimal("80"))
        company_review = self.approved_month(4, Decimal("100"))

        with CaptureQueriesContext(connection) as queries:
            result = evaluate_bonus(
                review,
                rule_set=self.rule_set,
                team_review=team_review,
                company_review=company_review,
            )

        self.assertEqual(result.eligibility_status, ELIGIBLE)
        self.assertLessEqual(
            len(queries),
            3,
            [query["sql"] for query in queries],
        )
