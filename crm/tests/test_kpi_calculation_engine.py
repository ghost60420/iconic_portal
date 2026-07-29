from datetime import UTC, date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from crm.models import (
    EmployeeProfile,
    KPIItemDefinition,
    KPIRoleTemplate,
    KPISettings,
    KPITemplateVersion,
)
from crm.services.kpi_assignments import assign_kpi_roles
from crm.services.kpi_calculation_engine import (
    CALCULATION_ENGINE_VERSION,
    FORMULA_VERSION,
    EmployeeCalculationInput,
    EmployeeRoleInput,
    KPIItemInput,
    KPIItemMetric,
    KPITemplateInput,
    KPICalculationEngine,
    KPIStatus,
    MeasurementType,
    ScoreRanges,
    ScoringDirection,
    calculate_employee_kpi_score,
)


class KPICalculationEngineUnitTests(SimpleTestCase):
    review_date = date(2026, 7, 1)
    calculated_at = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)

    def setUp(self):
        self.ranges = ScoreRanges(
            red_min=Decimal("0.00"),
            red_max=Decimal("69.99"),
            yellow_min=Decimal("70.00"),
            yellow_max=Decimal("84.99"),
            green_min=Decimal("85.00"),
            green_max=Decimal("100.00"),
            source="test_settings",
            source_version=4,
        )
        self.engine = KPICalculationEngine(
            score_ranges=self.ranges,
            calculated_at=self.calculated_at,
        )

    def _item(
        self,
        *,
        kpi_id=1,
        name="Delivery",
        weight=Decimal("100.00"),
        actual=Decimal("90"),
        target=None,
        measurement_type=MeasurementType.MANUAL_SCORE,
        direction=ScoringDirection.HIGHER_IS_BETTER,
        minimum=Decimal("0"),
        maximum=None,
        critical_red=False,
        critical_reason="",
        critical_trigger="",
        is_active=True,
        template_id=10,
        template_version=3,
        assignment_id=20,
        assignment_version=2,
        employee_id=30,
        status_ranges=None,
    ):
        return KPIItemInput(
            kpi_id=kpi_id,
            name=name,
            weight=weight,
            metric=KPIItemMetric(
                actual=actual,
                target=target,
                measurement_type=measurement_type,
                direction=direction,
                minimum=minimum,
                maximum=maximum,
                critical_red=critical_red,
                critical_reason=critical_reason,
                critical_trigger=critical_trigger,
            ),
            template_id=template_id,
            template_version=template_version,
            assignment_id=assignment_id,
            assignment_version=assignment_version,
            employee_id=employee_id,
            role_name="Project Manager",
            is_active=is_active,
            status_ranges=status_ranges,
        )

    def _template(self, items, *, template_id=10, assignment_id=20):
        return KPITemplateInput(
            template_id=template_id,
            template_name=f"Role {template_id}",
            template_version=3,
            items=tuple(items),
            assignment_id=assignment_id,
            assignment_version=2,
            employee_id=30,
        )

    def _role(
        self,
        *,
        assignment_id,
        template_id,
        score,
        weight,
        template_version=3,
        assignment_version=2,
    ):
        item = self._item(
            kpi_id=template_id * 10,
            actual=score,
            template_id=template_id,
            template_version=template_version,
            assignment_id=assignment_id,
            assignment_version=assignment_version,
        )
        template = KPITemplateInput(
            template_id=template_id,
            template_name=f"Role {template_id}",
            template_version=template_version,
            items=(item,),
            assignment_id=assignment_id,
            assignment_version=assignment_version,
            employee_id=30,
        )
        return EmployeeRoleInput(
            assignment_id=assignment_id,
            assignment_version=assignment_version,
            employee_id=30,
            role_weight=weight,
            template=template,
            start_date=date(2026, 1, 1),
        )

    def test_single_weighted_kpi_returns_normalized_result(self):
        result = self.engine.score_item(
            self._item(weight=Decimal("25.00"), actual=Decimal("88.25")),
            review_date=self.review_date,
        )

        self.assertEqual(result.score, Decimal("88.250000"))
        self.assertEqual(result.weighted_score, Decimal("22.062500"))
        self.assertEqual(result.status, KPIStatus.GREEN)
        self.assertEqual(result.calculation_version, CALCULATION_ENGINE_VERSION)
        self.assertEqual(result.formula_version, FORMULA_VERSION)
        self.assertEqual(result.template_version, 3)
        self.assertEqual(result.assignment_version, 2)

    def test_all_supported_measurement_types(self):
        cases = (
            (
                MeasurementType.PERCENTAGE,
                Decimal("45"),
                Decimal("50"),
                ScoringDirection.HIGHER_IS_BETTER,
                Decimal("90.000000"),
            ),
            (
                MeasurementType.COUNT,
                8,
                10,
                ScoringDirection.HIGHER_IS_BETTER,
                Decimal("80.000000"),
            ),
            (
                MeasurementType.CURRENCY,
                Decimal("750"),
                Decimal("1000"),
                ScoringDirection.HIGHER_IS_BETTER,
                Decimal("75.000000"),
            ),
            (
                MeasurementType.BOOLEAN,
                True,
                True,
                ScoringDirection.EXACT_TARGET,
                Decimal("100.000000"),
            ),
            (
                MeasurementType.MANUAL_SCORE,
                Decimal("82.5"),
                None,
                ScoringDirection.EXACT_TARGET,
                Decimal("82.500000"),
            ),
            (
                MeasurementType.DURATION,
                Decimal("10"),
                Decimal("5"),
                ScoringDirection.LOWER_IS_BETTER,
                Decimal("50.000000"),
            ),
            (
                MeasurementType.DECIMAL,
                Decimal("1.5"),
                Decimal("2"),
                ScoringDirection.HIGHER_IS_BETTER,
                Decimal("75.000000"),
            ),
        )

        for measurement_type, actual, target, direction, expected in cases:
            with self.subTest(measurement_type=measurement_type):
                result = self.engine.score_item(
                    self._item(
                        actual=actual,
                        target=target,
                        measurement_type=measurement_type,
                        direction=direction,
                    ),
                    review_date=self.review_date,
                )
                self.assertEqual(result.score, expected)

    def test_percentage_without_target_uses_normalized_percentage(self):
        result = self.engine.score_item(
            self._item(
                actual=Decimal("91.25"),
                measurement_type=MeasurementType.PERCENTAGE,
            ),
            review_date=self.review_date,
        )

        self.assertEqual(result.score, Decimal("91.250000"))

    def test_negative_values_are_rejected_by_default(self):
        with self.assertRaisesMessage(ValidationError, "Negative values are not allowed"):
            self.engine.score_item(
                self._item(
                    actual=Decimal("-1"),
                    target=Decimal("10"),
                    measurement_type=MeasurementType.DECIMAL,
                    minimum=None,
                ),
                review_date=self.review_date,
            )

    def test_manual_score_outside_zero_to_one_hundred_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "between 0 and 100"):
            self.engine.score_item(
                self._item(actual=Decimal("100.01")),
                review_date=self.review_date,
            )

    def test_count_rejects_fractional_values(self):
        with self.assertRaisesMessage(ValidationError, "whole numbers"):
            self.engine.score_item(
                self._item(
                    actual=Decimal("1.5"),
                    target=Decimal("2"),
                    measurement_type=MeasurementType.COUNT,
                ),
                review_date=self.review_date,
            )

    def test_configured_status_boundaries_are_not_hard_coded(self):
        custom_ranges = ScoreRanges(
            red_min=Decimal("0.00"),
            red_max=Decimal("74.99"),
            yellow_min=Decimal("75.00"),
            yellow_max=Decimal("89.99"),
            green_min=Decimal("90.00"),
            green_max=Decimal("100.00"),
            source="custom_settings",
            source_version=9,
        )
        engine = KPICalculationEngine(
            score_ranges=custom_ranges,
            calculated_at=self.calculated_at,
        )

        self.assertEqual(engine.status_for(Decimal("74.999")), KPIStatus.RED)
        self.assertEqual(engine.status_for(Decimal("75")), KPIStatus.YELLOW)
        self.assertEqual(engine.status_for(Decimal("89.999")), KPIStatus.YELLOW)
        self.assertEqual(engine.status_for(Decimal("90")), KPIStatus.GREEN)

    def test_template_weighted_total_and_decimal_accuracy(self):
        result = self.engine.score_template(
            self._template(
                (
                    self._item(
                        kpi_id=1,
                        name="Delivery",
                        weight=Decimal("33.33"),
                        actual=Decimal("90"),
                    ),
                    self._item(
                        kpi_id=2,
                        name="Quality",
                        weight=Decimal("33.33"),
                        actual=Decimal("80"),
                    ),
                    self._item(
                        kpi_id=3,
                        name="Documentation",
                        weight=Decimal("33.34"),
                        actual=Decimal("95"),
                    ),
                )
            ),
            review_date=self.review_date,
        )

        self.assertEqual(result.score, Decimal("88.334000"))
        self.assertEqual(result.weight, Decimal("100.000000"))
        self.assertEqual(result.status, KPIStatus.GREEN)

    def test_template_rejects_weights_below_or_above_one_hundred(self):
        for weights in (
            (Decimal("40"), Decimal("50")),
            (Decimal("60"), Decimal("50")),
        ):
            with self.subTest(weights=weights), self.assertRaisesMessage(
                ValidationError, "Weights must total exactly 100"
            ):
                self.engine.score_template(
                    self._template(
                        (
                            self._item(
                                kpi_id=1,
                                name="Delivery",
                                weight=weights[0],
                            ),
                            self._item(
                                kpi_id=2,
                                name="Quality",
                                weight=weights[1],
                            ),
                        )
                    ),
                    review_date=self.review_date,
                )

    def test_template_rejects_duplicate_definitions(self):
        with self.assertRaisesMessage(ValidationError, "Duplicate KPI definitions"):
            self.engine.score_template(
                self._template(
                    (
                        self._item(kpi_id=1, name="Quality", weight=Decimal("50")),
                        self._item(kpi_id=1, name="Delivery", weight=Decimal("50")),
                    )
                ),
                review_date=self.review_date,
            )

    def test_template_rejects_inactive_items(self):
        with self.assertRaisesMessage(ValidationError, "Inactive KPI items"):
            self.engine.score_template(
                self._template((self._item(is_active=False),)),
                review_date=self.review_date,
            )

    def test_single_role_employee_score(self):
        result = self.engine.score_employee(
            EmployeeCalculationInput(
                employee_id=30,
                review_date=self.review_date,
                roles=(
                    self._role(
                        assignment_id=20,
                        template_id=10,
                        score=Decimal("86"),
                        weight=Decimal("100"),
                    ),
                ),
            )
        )

        self.assertEqual(result.score, Decimal("86.000000"))
        self.assertEqual(result.roles[0].weighted_score, Decimal("86.000000"))
        self.assertEqual(result.status, KPIStatus.GREEN)

    def test_multiple_role_employee_example_equals_88_25(self):
        result = self.engine.score_employee(
            EmployeeCalculationInput(
                employee_id=30,
                review_date=self.review_date,
                roles=(
                    self._role(
                        assignment_id=20,
                        template_id=10,
                        score=Decimal("90"),
                        weight=Decimal("60"),
                    ),
                    self._role(
                        assignment_id=21,
                        template_id=11,
                        score=Decimal("80"),
                        weight=Decimal("25"),
                    ),
                    self._role(
                        assignment_id=22,
                        template_id=12,
                        score=Decimal("95"),
                        weight=Decimal("15"),
                    ),
                ),
            )
        )

        self.assertEqual(
            [role.weighted_score for role in result.roles],
            [Decimal("54.000000"), Decimal("20.000000"), Decimal("14.250000")],
        )
        self.assertEqual(result.score, Decimal("88.250000"))
        self.assertEqual(result.status, KPIStatus.GREEN)

    def test_employee_rejects_invalid_role_weight_total(self):
        with self.assertRaisesMessage(ValidationError, "Weights must total exactly 100"):
            self.engine.score_employee(
                EmployeeCalculationInput(
                    employee_id=30,
                    review_date=self.review_date,
                    roles=(
                        self._role(
                            assignment_id=20,
                            template_id=10,
                            score=Decimal("90"),
                            weight=Decimal("90"),
                        ),
                    ),
                )
            )

    def test_critical_red_preserves_score_and_forces_final_status(self):
        item = self._item(
            actual=Decimal("96"),
            critical_red=True,
            critical_reason="Approval was bypassed",
            critical_trigger="unapproved_shipment",
        )
        result = self.engine.score_employee(
            EmployeeCalculationInput(
                employee_id=30,
                review_date=self.review_date,
                roles=(
                    EmployeeRoleInput(
                        assignment_id=20,
                        assignment_version=2,
                        employee_id=30,
                        role_weight=Decimal("100"),
                        template=self._template((item,)),
                        start_date=date(2026, 1, 1),
                    ),
                ),
            )
        )

        self.assertEqual(result.score, Decimal("96.000000"))
        self.assertEqual(result.calculated_status, KPIStatus.GREEN)
        self.assertEqual(result.status, KPIStatus.RED)
        self.assertTrue(result.critical_red)
        detail = result.critical_red_details[0]
        self.assertEqual(detail.reason, "Approval was bypassed")
        self.assertEqual(detail.trigger, "unapproved_shipment")
        self.assertEqual(detail.source_kpi_id, 1)
        self.assertEqual(detail.affected_role_id, 10)
        self.assertEqual(detail.affected_employee_id, 30)

    def test_critical_red_requires_reason_and_trigger(self):
        for reason, trigger, expected in (
            ("", "event", "requires a reason"),
            ("Reason", "", "requires a trigger"),
        ):
            with self.subTest(reason=reason, trigger=trigger), self.assertRaisesMessage(
                ValidationError, expected
            ):
                self.engine.score_item(
                    self._item(
                        critical_red=True,
                        critical_reason=reason,
                        critical_trigger=trigger,
                    ),
                    review_date=self.review_date,
                )

    def test_historical_calculation_requires_effective_assignments(self):
        role = self._role(
            assignment_id=20,
            template_id=10,
            score=Decimal("90"),
            weight=Decimal("100"),
        )
        expired_role = EmployeeRoleInput(
            assignment_id=role.assignment_id,
            assignment_version=role.assignment_version,
            employee_id=role.employee_id,
            role_weight=role.role_weight,
            template=role.template,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        with self.assertRaisesMessage(ValidationError, "not effective"):
            self.engine.score_historical_review(
                EmployeeCalculationInput(
                    employee_id=30,
                    review_date=self.review_date,
                    roles=(expired_role,),
                )
            )

    def test_result_serialization_preserves_version_snapshots(self):
        result = self.engine.score_employee(
            EmployeeCalculationInput(
                employee_id=30,
                review_date=self.review_date,
                roles=(
                    self._role(
                        assignment_id=20,
                        template_id=10,
                        score=Decimal("88"),
                        weight=Decimal("100"),
                        template_version=7,
                        assignment_version=4,
                    ),
                ),
            )
        )

        payload = result.as_dict()
        self.assertEqual(payload["score"], "88.000000")
        self.assertEqual(payload["calculation_version"], CALCULATION_ENGINE_VERSION)
        self.assertEqual(payload["formula_version"], FORMULA_VERSION)
        self.assertEqual(payload["status_ranges"]["source_version"], 4)
        self.assertEqual(payload["template_versions"][0]["version"], 7)
        self.assertEqual(payload["assignment_versions"][0]["version"], 4)
        self.assertEqual(payload["review_date"], "2026-07-01")

    def test_unsupported_formula_version_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "is not supported"):
            KPICalculationEngine(
                score_ranges=self.ranges,
                calculated_at=self.calculated_at,
                formula_version="0.9",
            )


class KPICalculationEngineDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.actor = user_model.objects.create_superuser(
            "kpi-stage4-actor",
            "kpi-stage4-actor@example.com",
            "password",
        )
        cls.employee_user = user_model.objects.create_user("kpi-stage4-employee")
        cls.employee = EmployeeProfile.objects.get(user=cls.employee_user)
        cls.manager = user_model.objects.create_user("kpi-stage4-manager")
        KPISettings.objects.create(
            version=1,
            is_active=True,
            created_by=cls.actor,
        )

    @classmethod
    def _published_template(
        cls,
        suffix,
        *,
        weights=(Decimal("100.00"),),
        effective_start=date(2026, 1, 1),
        effective_end=None,
        version_number=1,
        retire=False,
        inactive_item=False,
        item_green_min=Decimal("85.00"),
        item_yellow_max=Decimal("84.99"),
    ):
        template, _created = KPIRoleTemplate.objects.get_or_create(
            code=f"stage4-{suffix}",
            defaults={
                "name": f"Stage 4 {suffix.title()}",
                "created_by": cls.actor,
            },
        )
        version = KPITemplateVersion.objects.create(
            template=template,
            version=version_number,
            effective_start=effective_start,
            effective_end=effective_end,
            created_by=cls.actor,
        )
        items = [
            KPIItemDefinition.objects.create(
                template_version=version,
                name=f"Metric {suffix} {index}",
                measurement_method="Manual score",
                target="100",
                weight=weight,
                data_source="Stage 4 tests",
                green_min=item_green_min,
                yellow_max=item_yellow_max,
            )
            for index, weight in enumerate(weights, start=1)
        ]
        if inactive_item:
            KPIItemDefinition.objects.create(
                template_version=version,
                name=f"Inactive {suffix}",
                measurement_method="Manual score",
                target="100",
                weight=Decimal("0.00"),
                data_source="Stage 4 tests",
                is_active=False,
            )
        version.status = KPITemplateVersion.STATUS_PUBLISHED
        version.published_by = cls.actor
        version.save()
        if retire:
            version.status = KPITemplateVersion.STATUS_RETIRED
            version.save()
        return template, version, items

    def _assign(self, employee, specs):
        return assign_kpi_roles(
            employee=employee,
            assignments=[
                {
                    "kpi_template": template,
                    "role_weight": weight,
                    "manager": self.manager,
                    "start_date": start_date,
                }
                for template, weight, start_date in specs
            ],
            actor=self.actor,
            reason="Stage 4 calculation test",
        )

    @staticmethod
    def _metrics(items, scores):
        return {
            item.pk: KPIItemMetric(
                actual=score,
                measurement_type=MeasurementType.MANUAL_SCORE,
            )
            for item, score in zip(items, scores)
        }

    def test_database_adapter_scores_current_assignment(self):
        template, _version, items = self._published_template("database-single")
        assignment = self._assign(
            self.employee,
            ((template, Decimal("100.00"), date(2026, 1, 1)),),
        )[0]

        result = calculate_employee_kpi_score(
            self.employee,
            review_date=date(2026, 7, 1),
            metrics_by_item_id=self._metrics(items, (Decimal("92"),)),
        )

        self.assertEqual(result.score, Decimal("92.000000"))
        self.assertEqual(result.roles[0].assignment_id, assignment.pk)
        self.assertEqual(
            result.roles[0].assignment_version, assignment.assignment_version
        )
        self.assertEqual(result.roles[0].template_version, 1)

    def test_historical_adapter_selects_effective_template_version(self):
        template, old_version, old_items = self._published_template(
            "historical",
            effective_start=date(2025, 1, 1),
            effective_end=date(2025, 12, 31),
            version_number=1,
            retire=True,
        )
        _template, new_version, new_items = self._published_template(
            "historical",
            effective_start=date(2026, 1, 1),
            version_number=2,
        )
        self._assign(
            self.employee,
            ((template, Decimal("100.00"), date(2025, 1, 1)),),
        )

        old_result = calculate_employee_kpi_score(
            self.employee,
            review_date=date(2025, 8, 1),
            metrics_by_item_id=self._metrics(old_items, (Decimal("81"),)),
        )
        new_result = calculate_employee_kpi_score(
            self.employee,
            review_date=date(2026, 7, 1),
            metrics_by_item_id=self._metrics(new_items, (Decimal("93"),)),
        )

        self.assertEqual(old_result.roles[0].template_version, old_version.version)
        self.assertEqual(new_result.roles[0].template_version, new_version.version)
        self.assertEqual(old_result.score, Decimal("81.000000"))
        self.assertEqual(new_result.score, Decimal("93.000000"))

    def test_database_adapter_uses_versioned_item_status_ranges(self):
        template, _version, items = self._published_template(
            "item-ranges",
            item_green_min=Decimal("90.00"),
            item_yellow_max=Decimal("89.99"),
        )
        self._assign(
            self.employee,
            ((template, Decimal("100.00"), date(2026, 1, 1)),),
        )

        result = calculate_employee_kpi_score(
            self.employee,
            review_date=date(2026, 7, 1),
            metrics_by_item_id=self._metrics(items, (Decimal("86"),)),
        )

        self.assertEqual(result.roles[0].template_result.items[0].status, KPIStatus.YELLOW)
        self.assertEqual(result.status, KPIStatus.GREEN)

    def test_database_adapter_rejects_inactive_item_result(self):
        template, version, items = self._published_template(
            "inactive", inactive_item=True
        )
        inactive = version.items.get(is_active=False)
        self._assign(
            self.employee,
            ((template, Decimal("100.00"), date(2026, 1, 1)),),
        )
        metrics = self._metrics(items, (Decimal("90"),))
        metrics[inactive.pk] = KPIItemMetric(
            actual=Decimal("90"),
            measurement_type=MeasurementType.MANUAL_SCORE,
        )

        with self.assertRaisesMessage(ValidationError, "Inactive KPI items"):
            calculate_employee_kpi_score(
                self.employee,
                review_date=date(2026, 7, 1),
                metrics_by_item_id=metrics,
            )

    def test_database_adapter_query_count_is_constant_without_n_plus_one(self):
        single_user = get_user_model().objects.create_user("kpi-stage4-query-single")
        single_employee = EmployeeProfile.objects.get(user=single_user)
        single_template, _version, single_items = self._published_template("query-one")
        self._assign(
            single_employee,
            ((single_template, Decimal("100.00"), date(2026, 1, 1)),),
        )

        multi_user = get_user_model().objects.create_user("kpi-stage4-query-multi")
        multi_employee = EmployeeProfile.objects.get(user=multi_user)
        multi_templates = [
            self._published_template(f"query-multi-{index}")
            for index in range(3)
        ]
        self._assign(
            multi_employee,
            tuple(
                (
                    template,
                    weight,
                    date(2026, 1, 1),
                )
                for (template, _version, _items), weight in zip(
                    multi_templates,
                    (Decimal("33.33"), Decimal("33.33"), Decimal("33.34")),
                )
            ),
        )
        multi_metrics = {}
        for _template, _version, items in multi_templates:
            multi_metrics.update(self._metrics(items, (Decimal("90"),)))

        with self.assertNumQueries(4):
            single_result = calculate_employee_kpi_score(
                single_employee,
                review_date=date(2026, 7, 1),
                metrics_by_item_id=self._metrics(
                    single_items, (Decimal("90"),)
                ),
            )
        with self.assertNumQueries(4):
            multi_result = calculate_employee_kpi_score(
                multi_employee,
                review_date=date(2026, 7, 1),
                metrics_by_item_id=multi_metrics,
            )

        self.assertEqual(single_result.score, Decimal("90.000000"))
        self.assertEqual(multi_result.score, Decimal("90.000000"))

    def test_database_adapter_performs_no_database_writes(self):
        template, _version, items = self._published_template("read-only")
        self._assign(
            self.employee,
            ((template, Decimal("100.00"), date(2026, 1, 1)),),
        )

        with CaptureQueriesContext(connection) as queries:
            calculate_employee_kpi_score(
                self.employee,
                review_date=date(2026, 7, 1),
                metrics_by_item_id=self._metrics(items, (Decimal("90"),)),
            )

        write_prefixes = ("INSERT", "UPDATE", "DELETE", "REPLACE")
        self.assertFalse(
            any(
                query["sql"].lstrip().upper().startswith(write_prefixes)
                for query in queries
            )
        )

    def test_reused_engine_caches_immutable_template_definitions(self):
        template, _version, items = self._published_template("definition-cache")
        self._assign(
            self.employee,
            ((template, Decimal("100.00"), date(2026, 1, 1)),),
        )
        metrics = self._metrics(items, (Decimal("90"),))
        engine = KPICalculationEngine()

        with self.assertNumQueries(3):
            first = engine.score_employee_from_models(
                self.employee,
                review_date=date(2026, 7, 1),
                metrics_by_item_id=metrics,
            )
        with self.assertNumQueries(2):
            second = engine.score_employee_from_models(
                self.employee,
                review_date=date(2026, 7, 1),
                metrics_by_item_id=metrics,
            )

        self.assertEqual(first, second)
