from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Any, Iterable, Mapping

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q, prefetch_related_objects
from django.utils import timezone

from crm.models_employee import EmployeeProfile
from crm.models_kpi import (
    KPIItemDefinition,
    KPISettings,
    KPITemplateVersion,
)
from crm.services.kpi_assignments import assignments_for_date


CALCULATION_ENGINE_VERSION = "1.0.0"
FORMULA_VERSION = "1.0"
PERCENT_MIN = Decimal("0")
PERCENT_MAX = Decimal("100")
PERCENT_STEP = Decimal("0.01")
INTERNAL_QUANTUM = Decimal("0.000001")


class KPIStatus(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class MeasurementType(str, Enum):
    PERCENTAGE = "percentage"
    COUNT = "count"
    CURRENCY = "currency"
    BOOLEAN = "boolean"
    MANUAL_SCORE = "manual_score"
    DURATION = "duration"
    DECIMAL = "decimal"


class ScoringDirection(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    EXACT_TARGET = "exact_target"


def _decimal(value: Any, field: str, *, required: bool = True) -> Decimal | None:
    if value is None:
        if required:
            raise ValidationError({field: f"{field.replace('_', ' ').title()} is required."})
        return None
    if isinstance(value, bool):
        raise ValidationError({field: "Boolean values are not valid numbers."})
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({field: "Enter a valid number."}) from None
    if not normalized.is_finite():
        raise ValidationError({field: "Enter a finite number."})
    return normalized


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(INTERNAL_QUANTUM, rounding=ROUND_HALF_UP)


def _bounded_score(value: Decimal) -> Decimal:
    return _quantize(min(max(value, PERCENT_MIN), PERCENT_MAX))


def _enum_value(enum_class, value, field):
    try:
        return value if isinstance(value, enum_class) else enum_class(value)
    except (TypeError, ValueError):
        choices = ", ".join(member.value for member in enum_class)
        raise ValidationError({field: f"Use one of: {choices}."}) from None


def _json_ready(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


class SerializableCalculation:
    def as_dict(self):
        return _json_ready(asdict(self))


@dataclass(frozen=True, slots=True)
class ScoreRanges(SerializableCalculation):
    red_min: Decimal
    red_max: Decimal
    yellow_min: Decimal
    yellow_max: Decimal
    green_min: Decimal
    green_max: Decimal
    source: str
    source_version: int

    def __post_init__(self):
        values = (
            self.red_min,
            self.red_max,
            self.yellow_min,
            self.yellow_max,
            self.green_min,
            self.green_max,
        )
        if any(value < PERCENT_MIN or value > PERCENT_MAX for value in values):
            raise ValidationError(
                {"status_ranges": "Status ranges must stay between 0 and 100."}
            )
        if self.red_min != PERCENT_MIN or self.green_max != PERCENT_MAX:
            raise ValidationError(
                {"status_ranges": "Status ranges must cover 0 through 100."}
            )
        if (
            self.red_min > self.red_max
            or self.yellow_min > self.yellow_max
            or self.green_min > self.green_max
            or self.yellow_min != self.red_max + PERCENT_STEP
            or self.green_min != self.yellow_max + PERCENT_STEP
        ):
            raise ValidationError(
                {
                    "status_ranges": (
                        "Status ranges must be ordered and contiguous at "
                        "two-decimal precision."
                    )
                }
            )

    @classmethod
    def from_settings(cls, settings):
        return cls(
            red_min=settings.red_min,
            red_max=settings.red_max,
            yellow_min=settings.yellow_min,
            yellow_max=settings.yellow_max,
            green_min=settings.green_min,
            green_max=settings.green_max,
            source="kpi_settings",
            source_version=settings.version,
        )

    @classmethod
    def from_item_definition(cls, item):
        return cls(
            red_min=item.red_min,
            red_max=item.red_max,
            yellow_min=item.yellow_min,
            yellow_max=item.yellow_max,
            green_min=item.green_min,
            green_max=item.green_max,
            source="kpi_item_definition",
            source_version=item.template_version.version,
        )

    def status_for(self, score: Decimal) -> KPIStatus:
        score = validate_score_range(score)
        if score >= self.green_min:
            return KPIStatus.GREEN
        if score >= self.yellow_min:
            return KPIStatus.YELLOW
        return KPIStatus.RED


@dataclass(frozen=True, slots=True)
class KPIItemMetric(SerializableCalculation):
    actual: Any
    measurement_type: MeasurementType | str
    target: Any = None
    direction: ScoringDirection | str = ScoringDirection.HIGHER_IS_BETTER
    minimum: Any = Decimal("0")
    maximum: Any = None
    allow_negative: bool = False
    critical_red: bool = False
    critical_reason: str = ""
    critical_trigger: str = ""
    critical_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class KPIItemInput(SerializableCalculation):
    kpi_id: int | str
    name: str
    weight: Decimal
    metric: KPIItemMetric
    template_id: int | str
    template_version: int
    assignment_id: int | str | None = None
    assignment_version: int | None = None
    employee_id: int | str | None = None
    role_name: str = ""
    is_active: bool = True
    status_ranges: ScoreRanges | None = None


@dataclass(frozen=True, slots=True)
class KPITemplateInput(SerializableCalculation):
    template_id: int | str
    template_name: str
    template_version: int
    items: tuple[KPIItemInput, ...]
    assignment_id: int | str | None = None
    assignment_version: int | None = None
    employee_id: int | str | None = None


@dataclass(frozen=True, slots=True)
class EmployeeRoleInput(SerializableCalculation):
    assignment_id: int | str
    assignment_version: int
    employee_id: int | str
    role_weight: Decimal
    template: KPITemplateInput
    start_date: date
    end_date: date | None = None
    is_active: bool = True
    is_archived: bool = False

    def is_effective(self, review_date: date) -> bool:
        return (
            self.is_active
            and not self.is_archived
            and self.start_date <= review_date
            and (self.end_date is None or self.end_date >= review_date)
        )


@dataclass(frozen=True, slots=True)
class EmployeeCalculationInput(SerializableCalculation):
    employee_id: int | str
    review_date: date
    roles: tuple[EmployeeRoleInput, ...]


@dataclass(frozen=True, slots=True)
class CriticalRedDetail(SerializableCalculation):
    reason: str
    trigger: str
    triggered_at: datetime
    source_kpi_id: int | str
    source_kpi_name: str
    affected_role_id: int | str | None
    affected_role_name: str
    affected_employee_id: int | str | None


@dataclass(frozen=True, slots=True)
class WeightedComponent(SerializableCalculation):
    key: int | str
    score: Decimal
    weight: Decimal


@dataclass(frozen=True, slots=True)
class WeightedAverageResult(SerializableCalculation):
    score: Decimal
    weight: Decimal
    weighted_score: Decimal
    status: KPIStatus
    critical_red: bool
    reason: str
    calculated_at: datetime
    calculation_version: str
    formula_version: str
    review_date: date
    status_ranges: ScoreRanges
    components: tuple[WeightedComponent, ...]


@dataclass(frozen=True, slots=True)
class KPIItemScoreResult(SerializableCalculation):
    kpi_id: int | str
    name: str
    actual: Any
    target: Any
    minimum: Any
    maximum: Any
    measurement_type: MeasurementType
    direction: ScoringDirection
    score: Decimal
    weight: Decimal
    weighted_score: Decimal
    calculated_status: KPIStatus
    status: KPIStatus
    critical_red: bool
    reason: str
    trigger: str
    calculated_at: datetime
    calculation_version: str
    formula_version: str
    review_date: date
    template_id: int | str
    template_version: int
    assignment_id: int | str | None
    assignment_version: int | None
    status_ranges: ScoreRanges
    critical_red_details: tuple[CriticalRedDetail, ...]


@dataclass(frozen=True, slots=True)
class KPITemplateScoreResult(SerializableCalculation):
    template_id: int | str
    template_name: str
    template_version: int
    score: Decimal
    weight: Decimal
    weighted_score: Decimal
    calculated_status: KPIStatus
    status: KPIStatus
    critical_red: bool
    reason: str
    calculated_at: datetime
    calculation_version: str
    formula_version: str
    review_date: date
    status_ranges: ScoreRanges
    assignment_id: int | str | None
    assignment_version: int | None
    items: tuple[KPIItemScoreResult, ...]
    critical_red_details: tuple[CriticalRedDetail, ...]


@dataclass(frozen=True, slots=True)
class EmployeeRoleScoreResult(SerializableCalculation):
    assignment_id: int | str
    assignment_version: int
    employee_id: int | str
    template_id: int | str
    template_name: str
    template_version: int
    score: Decimal
    weight: Decimal
    weighted_score: Decimal
    calculated_status: KPIStatus
    status: KPIStatus
    critical_red: bool
    reason: str
    calculated_at: datetime
    calculation_version: str
    formula_version: str
    review_date: date
    status_ranges: ScoreRanges
    template_result: KPITemplateScoreResult
    critical_red_details: tuple[CriticalRedDetail, ...]


@dataclass(frozen=True, slots=True)
class VersionReference(SerializableCalculation):
    object_id: int | str
    version: int


@dataclass(frozen=True, slots=True)
class EmployeeScoreResult(SerializableCalculation):
    employee_id: int | str
    score: Decimal
    weight: Decimal
    weighted_score: Decimal
    calculated_status: KPIStatus
    status: KPIStatus
    critical_red: bool
    reason: str
    calculated_at: datetime
    calculation_version: str
    formula_version: str
    review_date: date
    status_ranges: ScoreRanges
    template_versions: tuple[VersionReference, ...]
    assignment_versions: tuple[VersionReference, ...]
    roles: tuple[EmployeeRoleScoreResult, ...]
    critical_red_details: tuple[CriticalRedDetail, ...]


def validate_score_range(score) -> Decimal:
    score = _decimal(score, "score")
    if score < PERCENT_MIN or score > PERCENT_MAX:
        raise ValidationError({"score": "Score must be between 0 and 100."})
    return score


def _normalize_numeric_metric(metric: KPIItemMetric):
    measurement_type = _enum_value(
        MeasurementType, metric.measurement_type, "measurement_type"
    )
    direction = _enum_value(ScoringDirection, metric.direction, "direction")
    actual = _decimal(metric.actual, "actual")
    target = _decimal(metric.target, "target", required=False)
    minimum = _decimal(metric.minimum, "minimum", required=False)
    maximum = _decimal(metric.maximum, "maximum", required=False)

    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValidationError({"maximum": "Maximum cannot be below minimum."})
    if not metric.allow_negative:
        for field, value in (
            ("actual", actual),
            ("target", target),
            ("minimum", minimum),
            ("maximum", maximum),
        ):
            if value is not None and value < 0:
                raise ValidationError({field: "Negative values are not allowed."})
    if minimum is not None and actual < minimum:
        raise ValidationError({"actual": "Actual result is below the allowed minimum."})
    if maximum is not None and actual > maximum:
        raise ValidationError({"actual": "Actual result exceeds the allowed maximum."})

    if measurement_type == MeasurementType.COUNT:
        for field, value in (("actual", actual), ("target", target)):
            if value is not None and value != value.to_integral_value():
                raise ValidationError({field: "Count values must be whole numbers."})
    if measurement_type == MeasurementType.PERCENTAGE:
        for field, value in (("actual", actual), ("target", target)):
            if value is not None and value > PERCENT_MAX:
                raise ValidationError(
                    {field: "Percentage values cannot exceed 100."}
                )
    return measurement_type, direction, actual, target, minimum, maximum


def _score_manual(metric: KPIItemMetric):
    actual = _decimal(metric.actual, "actual")
    return (
        MeasurementType.MANUAL_SCORE,
        ScoringDirection.EXACT_TARGET,
        validate_score_range(actual),
        _decimal(metric.target, "target", required=False),
        PERCENT_MIN,
        PERCENT_MAX,
    )


def _score_boolean(metric: KPIItemMetric):
    if not isinstance(metric.actual, bool):
        raise ValidationError({"actual": "Boolean KPI actual must be true or false."})
    target = True if metric.target is None else metric.target
    if not isinstance(target, bool):
        raise ValidationError({"target": "Boolean KPI target must be true or false."})
    score = PERCENT_MAX if metric.actual == target else PERCENT_MIN
    return (
        MeasurementType.BOOLEAN,
        ScoringDirection.EXACT_TARGET,
        score,
        target,
        False,
        True,
    )


def _score_numeric(metric: KPIItemMetric):
    (
        measurement_type,
        direction,
        actual,
        target,
        minimum,
        maximum,
    ) = _normalize_numeric_metric(metric)
    if target is None:
        if measurement_type == MeasurementType.PERCENTAGE:
            score = actual
        else:
            raise ValidationError(
                {"target": "A target is required for this measurement type."}
            )
    elif direction == ScoringDirection.EXACT_TARGET:
        score = PERCENT_MAX if actual == target else PERCENT_MIN
    elif direction == ScoringDirection.HIGHER_IS_BETTER:
        if target <= 0:
            raise ValidationError(
                {"target": "Higher-is-better targets must be greater than zero."}
            )
        score = (actual / target) * PERCENT_MAX
    else:
        if target < 0:
            raise ValidationError(
                {"target": "Lower-is-better targets cannot be negative."}
            )
        if actual <= target:
            score = PERCENT_MAX
        elif actual == 0:
            score = PERCENT_MAX
        else:
            score = (target / actual) * PERCENT_MAX
    return (
        measurement_type,
        direction,
        _bounded_score(score),
        target,
        minimum,
        maximum,
    )


MEASUREMENT_SCORERS = {
    MeasurementType.MANUAL_SCORE: _score_manual,
    MeasurementType.BOOLEAN: _score_boolean,
    MeasurementType.PERCENTAGE: _score_numeric,
    MeasurementType.COUNT: _score_numeric,
    MeasurementType.CURRENCY: _score_numeric,
    MeasurementType.DURATION: _score_numeric,
    MeasurementType.DECIMAL: _score_numeric,
}


class KPICalculationEngine:
    """Single service entry point for all KPI score calculations."""

    def __init__(
        self,
        *,
        settings: KPISettings | None = None,
        score_ranges: ScoreRanges | None = None,
        calculated_at: datetime | None = None,
        formula_version: str = FORMULA_VERSION,
    ):
        if settings is not None and score_ranges is not None:
            raise ValidationError(
                {"settings": "Provide KPI settings or score ranges, not both."}
            )
        if settings is None and score_ranges is None:
            settings = self._active_settings()
        self.score_ranges = (
            score_ranges if score_ranges is not None else ScoreRanges.from_settings(settings)
        )
        self.settings_version = (
            settings.version if settings is not None else score_ranges.source_version
        )
        self.calculated_at = calculated_at or timezone.now()
        if timezone.is_naive(self.calculated_at):
            raise ValidationError(
                {"calculated_at": "Calculated time must include timezone information."}
            )
        if formula_version != FORMULA_VERSION:
            raise ValidationError(
                {
                    "formula_version": (
                        f"Formula version {formula_version} is not supported by "
                        f"engine {CALCULATION_ENGINE_VERSION}."
                    )
                }
            )
        self.formula_version = formula_version
        self._template_definition_cache = {}

    @staticmethod
    def _active_settings():
        try:
            return KPISettings.objects.only(
                "version",
                "red_min",
                "red_max",
                "yellow_min",
                "yellow_max",
                "green_min",
                "green_max",
            ).get(is_active=True)
        except KPISettings.DoesNotExist:
            raise ValidationError(
                {"settings": "An active KPI settings version is required."}
            ) from None

    def status_for(self, score) -> KPIStatus:
        return self.score_ranges.status_for(score)

    def calculate_weighted_average(
        self,
        components: Iterable[WeightedComponent],
        *,
        review_date: date,
        expected_weight: Decimal = PERCENT_MAX,
    ) -> WeightedAverageResult:
        components = tuple(components)
        if not components:
            raise ValidationError({"components": "At least one score is required."})
        seen = set()
        for component in components:
            if component.key in seen:
                raise ValidationError({"components": "Duplicate score components found."})
            seen.add(component.key)
            validate_score_range(component.score)
            if component.weight <= 0 or component.weight > PERCENT_MAX:
                raise ValidationError(
                    {"weight": "Weights must be greater than 0 and at most 100."}
                )
        total_weight = sum(
            (Decimal(str(component.weight)) for component in components),
            Decimal("0"),
        )
        if total_weight != expected_weight:
            raise ValidationError(
                {
                    "weight": (
                        f"Weights must total exactly {expected_weight}; "
                        f"found {total_weight}."
                    )
                }
            )
        weighted_score = sum(
            (
                Decimal(str(component.score))
                * Decimal(str(component.weight))
                / PERCENT_MAX
                for component in components
            ),
            Decimal("0"),
        )
        score = _bounded_score(weighted_score * PERCENT_MAX / total_weight)
        return WeightedAverageResult(
            score=score,
            weight=_quantize(total_weight),
            weighted_score=_quantize(weighted_score),
            status=self.status_for(score),
            critical_red=False,
            reason="",
            calculated_at=self.calculated_at,
            calculation_version=CALCULATION_ENGINE_VERSION,
            formula_version=self.formula_version,
            review_date=review_date,
            status_ranges=self.score_ranges,
            components=components,
        )

    def score_item(
        self, item: KPIItemInput, *, review_date: date
    ) -> KPIItemScoreResult:
        if not item.is_active:
            raise ValidationError({"kpi_item": "Inactive KPI items cannot be scored."})
        weight = _decimal(item.weight, "weight")
        if weight <= 0 or weight > PERCENT_MAX:
            raise ValidationError(
                {"weight": "KPI item weight must be greater than 0 and at most 100."}
            )
        measurement_type = _enum_value(
            MeasurementType, item.metric.measurement_type, "measurement_type"
        )
        scorer = MEASUREMENT_SCORERS.get(measurement_type)
        if scorer is None:
            raise ValidationError(
                {"measurement_type": "No scorer is registered for this measurement."}
            )
        (
            measurement_type,
            direction,
            score,
            target,
            minimum,
            maximum,
        ) = scorer(item.metric)
        score = validate_score_range(score)
        ranges = item.status_ranges or self.score_ranges
        calculated_status = ranges.status_for(score)
        critical_details = ()
        if item.metric.critical_red:
            reason = item.metric.critical_reason.strip()
            trigger = item.metric.critical_trigger.strip()
            if not reason:
                raise ValidationError(
                    {"critical_reason": "Critical Red requires a reason."}
                )
            if not trigger:
                raise ValidationError(
                    {"critical_trigger": "Critical Red requires a trigger."}
                )
            triggered_at = item.metric.critical_time or self.calculated_at
            if timezone.is_naive(triggered_at):
                raise ValidationError(
                    {"critical_time": "Critical Red time must include timezone information."}
                )
            critical_details = (
                CriticalRedDetail(
                    reason=reason,
                    trigger=trigger,
                    triggered_at=triggered_at,
                    source_kpi_id=item.kpi_id,
                    source_kpi_name=item.name,
                    affected_role_id=item.template_id,
                    affected_role_name=item.role_name,
                    affected_employee_id=item.employee_id,
                ),
            )
        else:
            reason = ""
            trigger = ""
        return KPIItemScoreResult(
            kpi_id=item.kpi_id,
            name=item.name,
            actual=item.metric.actual,
            target=target,
            minimum=minimum,
            maximum=maximum,
            measurement_type=measurement_type,
            direction=direction,
            score=_quantize(score),
            weight=_quantize(weight),
            weighted_score=_quantize(score * weight / PERCENT_MAX),
            calculated_status=calculated_status,
            status=KPIStatus.RED if critical_details else calculated_status,
            critical_red=bool(critical_details),
            reason=reason,
            trigger=trigger,
            calculated_at=self.calculated_at,
            calculation_version=CALCULATION_ENGINE_VERSION,
            formula_version=self.formula_version,
            review_date=review_date,
            template_id=item.template_id,
            template_version=item.template_version,
            assignment_id=item.assignment_id,
            assignment_version=item.assignment_version,
            status_ranges=ranges,
            critical_red_details=critical_details,
        )

    def score_template(
        self, template: KPITemplateInput, *, review_date: date
    ) -> KPITemplateScoreResult:
        if not template.items:
            raise ValidationError({"items": "A KPI template requires active KPI items."})
        ids = [item.kpi_id for item in template.items]
        names = [item.name.strip().casefold() for item in template.items]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValidationError({"items": "Duplicate KPI definitions are not allowed."})
        for item in template.items:
            if item.template_id != template.template_id:
                raise ValidationError(
                    {"items": "Every KPI item must belong to the scored template."}
                )
            if item.template_version != template.template_version:
                raise ValidationError(
                    {"items": "Every KPI item must use the scored template version."}
                )
            if not item.is_active:
                raise ValidationError({"items": "Inactive KPI items cannot be scored."})
        item_results = tuple(
            self.score_item(item, review_date=review_date) for item in template.items
        )
        average = self.calculate_weighted_average(
            (
                WeightedComponent(
                    key=result.kpi_id,
                    score=result.score,
                    weight=result.weight,
                )
                for result in item_results
            ),
            review_date=review_date,
        )
        critical_details = tuple(
            detail
            for result in item_results
            for detail in result.critical_red_details
        )
        calculated_status = self.status_for(average.score)
        return KPITemplateScoreResult(
            template_id=template.template_id,
            template_name=template.template_name,
            template_version=template.template_version,
            score=average.score,
            weight=average.weight,
            weighted_score=average.weighted_score,
            calculated_status=calculated_status,
            status=KPIStatus.RED if critical_details else calculated_status,
            critical_red=bool(critical_details),
            reason=critical_details[0].reason if critical_details else "",
            calculated_at=self.calculated_at,
            calculation_version=CALCULATION_ENGINE_VERSION,
            formula_version=self.formula_version,
            review_date=review_date,
            status_ranges=self.score_ranges,
            assignment_id=template.assignment_id,
            assignment_version=template.assignment_version,
            items=item_results,
            critical_red_details=critical_details,
        )

    def score_role(
        self, role: EmployeeRoleInput, *, review_date: date
    ) -> EmployeeRoleScoreResult:
        if not role.is_effective(review_date):
            raise ValidationError(
                {"assignment": "The KPI role assignment is not effective on the review date."}
            )
        if role.template.assignment_id != role.assignment_id:
            raise ValidationError(
                {"assignment": "Template input does not match the role assignment."}
            )
        if role.template.assignment_version != role.assignment_version:
            raise ValidationError(
                {"assignment_version": "Template input has a different assignment version."}
            )
        if role.template.employee_id != role.employee_id:
            raise ValidationError(
                {"employee": "Template input does not match the assigned employee."}
            )
        role_weight = _decimal(role.role_weight, "role_weight")
        if role_weight <= 0 or role_weight > PERCENT_MAX:
            raise ValidationError(
                {"role_weight": "Role weight must be greater than 0 and at most 100."}
            )
        template_result = self.score_template(
            role.template, review_date=review_date
        )
        return EmployeeRoleScoreResult(
            assignment_id=role.assignment_id,
            assignment_version=role.assignment_version,
            employee_id=role.employee_id,
            template_id=template_result.template_id,
            template_name=template_result.template_name,
            template_version=template_result.template_version,
            score=template_result.score,
            weight=_quantize(role_weight),
            weighted_score=_quantize(
                template_result.score * role_weight / PERCENT_MAX
            ),
            calculated_status=template_result.calculated_status,
            status=template_result.status,
            critical_red=template_result.critical_red,
            reason=template_result.reason,
            calculated_at=self.calculated_at,
            calculation_version=CALCULATION_ENGINE_VERSION,
            formula_version=self.formula_version,
            review_date=review_date,
            status_ranges=self.score_ranges,
            template_result=template_result,
            critical_red_details=template_result.critical_red_details,
        )

    def score_employee(
        self, calculation: EmployeeCalculationInput
    ) -> EmployeeScoreResult:
        if not calculation.roles:
            raise ValidationError(
                {"assignments": "The employee has no KPI roles for this review date."}
            )
        assignment_ids = [role.assignment_id for role in calculation.roles]
        template_ids = [role.template.template_id for role in calculation.roles]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValidationError({"assignments": "Duplicate assignments are not allowed."})
        if len(template_ids) != len(set(template_ids)):
            raise ValidationError(
                {"assignments": "Duplicate KPI templates are not allowed for one date."}
            )
        for role in calculation.roles:
            if role.employee_id != calculation.employee_id:
                raise ValidationError(
                    {"employee": "Every KPI role must belong to the scored employee."}
                )
        role_results = tuple(
            self.score_role(role, review_date=calculation.review_date)
            for role in calculation.roles
        )
        average = self.calculate_weighted_average(
            (
                WeightedComponent(
                    key=result.assignment_id,
                    score=result.score,
                    weight=result.weight,
                )
                for result in role_results
            ),
            review_date=calculation.review_date,
        )
        critical_details = tuple(
            detail
            for result in role_results
            for detail in result.critical_red_details
        )
        calculated_status = self.status_for(average.score)
        return EmployeeScoreResult(
            employee_id=calculation.employee_id,
            score=average.score,
            weight=average.weight,
            weighted_score=average.weighted_score,
            calculated_status=calculated_status,
            status=KPIStatus.RED if critical_details else calculated_status,
            critical_red=bool(critical_details),
            reason=critical_details[0].reason if critical_details else "",
            calculated_at=self.calculated_at,
            calculation_version=CALCULATION_ENGINE_VERSION,
            formula_version=self.formula_version,
            review_date=calculation.review_date,
            status_ranges=self.score_ranges,
            template_versions=tuple(
                VersionReference(
                    object_id=result.template_id,
                    version=result.template_version,
                )
                for result in role_results
            ),
            assignment_versions=tuple(
                VersionReference(
                    object_id=result.assignment_id,
                    version=result.assignment_version,
                )
                for result in role_results
            ),
            roles=role_results,
            critical_red_details=critical_details,
        )

    def score_historical_review(
        self, calculation: EmployeeCalculationInput
    ) -> EmployeeScoreResult:
        return self.score_employee(calculation)

    @staticmethod
    def detect_critical_red(result) -> tuple[CriticalRedDetail, ...]:
        return tuple(getattr(result, "critical_red_details", ()))

    def score_employee_from_models(
        self,
        employee,
        *,
        review_date: date,
        metrics_by_item_id: Mapping[int, KPIItemMetric],
    ) -> EmployeeScoreResult:
        employee = self._resolve_employee(employee)
        assignments = list(assignments_for_date(employee, review_date))
        if not assignments:
            raise ValidationError(
                {"assignments": "The employee has no KPI roles for this review date."}
            )
        assignment_total = sum(
            (assignment.role_weight for assignment in assignments),
            Decimal("0"),
        )
        if assignment_total != PERCENT_MAX:
            raise ValidationError(
                {
                    "role_weight": (
                        "Active KPI role weights must total exactly 100; "
                        f"found {assignment_total}."
                    )
                }
            )
        template_ids = {assignment.kpi_template_id for assignment in assignments}
        versions = self._effective_template_versions(template_ids, review_date)
        selected_by_template = {}
        for version in versions:
            selected_by_template.setdefault(version.template_id, []).append(version)
        for template_id in template_ids:
            candidates = selected_by_template.get(template_id, [])
            if len(candidates) != 1:
                raise ValidationError(
                    {
                        "template_version": (
                            "Exactly one effective published or retired template "
                            f"version is required for template {template_id}; "
                            f"found {len(candidates)}."
                        )
                    }
                )

        used_metric_ids = set()
        role_inputs = []
        for assignment in assignments:
            version = selected_by_template[assignment.kpi_template_id][0]
            cache_key = (version.template_id, version.version)
            definitions = self._template_definition_cache[cache_key]
            active_definitions = [item for item in definitions if item.is_active]
            inactive_ids = {item.pk for item in definitions if not item.is_active}
            supplied_inactive = inactive_ids.intersection(metrics_by_item_id)
            if supplied_inactive:
                raise ValidationError(
                    {
                        "metrics": (
                            "Inactive KPI items cannot be scored: "
                            + ", ".join(str(item_id) for item_id in sorted(supplied_inactive))
                        )
                    }
                )
            item_inputs = []
            for definition in active_definitions:
                metric = metrics_by_item_id.get(definition.pk)
                if metric is None:
                    raise ValidationError(
                        {
                            "metrics": (
                                f"A result is required for KPI item {definition.pk} "
                                f"({definition.name})."
                            )
                        }
                    )
                if not isinstance(metric, KPIItemMetric):
                    raise ValidationError(
                        {"metrics": "Each KPI result must be a KPIItemMetric object."}
                    )
                used_metric_ids.add(definition.pk)
                item_inputs.append(
                    KPIItemInput(
                        kpi_id=definition.pk,
                        name=definition.name,
                        weight=definition.weight,
                        metric=metric,
                        template_id=version.template_id,
                        template_version=version.version,
                        assignment_id=assignment.pk,
                        assignment_version=assignment.assignment_version,
                        employee_id=employee.pk,
                        role_name=version.template.name,
                        is_active=definition.is_active,
                        status_ranges=ScoreRanges.from_item_definition(definition),
                    )
                )
            template_input = KPITemplateInput(
                template_id=version.template_id,
                template_name=version.template.name,
                template_version=version.version,
                items=tuple(item_inputs),
                assignment_id=assignment.pk,
                assignment_version=assignment.assignment_version,
                employee_id=employee.pk,
            )
            role_inputs.append(
                EmployeeRoleInput(
                    assignment_id=assignment.pk,
                    assignment_version=assignment.assignment_version,
                    employee_id=employee.pk,
                    role_weight=assignment.role_weight,
                    template=template_input,
                    start_date=assignment.start_date,
                    end_date=assignment.end_date,
                    is_active=assignment.is_active,
                    is_archived=assignment.is_archived,
                )
            )
        unknown_metric_ids = set(metrics_by_item_id) - used_metric_ids
        if unknown_metric_ids:
            raise ValidationError(
                {
                    "metrics": (
                        "Results were supplied for unknown or non-effective KPI items: "
                        + ", ".join(str(item_id) for item_id in sorted(unknown_metric_ids))
                    )
                }
            )
        return self.score_employee(
            EmployeeCalculationInput(
                employee_id=employee.pk,
                review_date=review_date,
                roles=tuple(role_inputs),
            )
        )

    @staticmethod
    def _resolve_employee(employee):
        if isinstance(employee, EmployeeProfile) and employee.pk:
            return employee
        employee_id = getattr(employee, "pk", employee)
        if not employee_id:
            raise ValidationError({"employee": "A valid employee is required."})
        resolved = EmployeeProfile.objects.filter(pk=employee_id).first()
        if resolved is None:
            raise ValidationError({"employee": "The selected employee does not exist."})
        return resolved

    def _effective_template_versions(self, template_ids, review_date):
        item_queryset = KPIItemDefinition.objects.order_by("sort_order", "id")
        versions = list(
            KPITemplateVersion.objects.filter(
                template_id__in=template_ids,
                status__in=(
                    KPITemplateVersion.STATUS_PUBLISHED,
                    KPITemplateVersion.STATUS_RETIRED,
                ),
            )
            .filter(
                Q(effective_start__isnull=True)
                | Q(effective_start__lte=review_date)
            )
            .filter(Q(effective_end__isnull=True) | Q(effective_end__gte=review_date))
            .select_related("template")
            .order_by("template_id", "-version")
        )
        uncached_versions = [
            version
            for version in versions
            if (version.template_id, version.version)
            not in self._template_definition_cache
        ]
        if uncached_versions:
            prefetch_related_objects(
                uncached_versions,
                Prefetch("items", queryset=item_queryset),
            )
            for version in uncached_versions:
                self._template_definition_cache[
                    (version.template_id, version.version)
                ] = tuple(version.items.all())
        return versions


def calculate_employee_kpi_score(
    employee,
    *,
    review_date: date,
    metrics_by_item_id: Mapping[int, KPIItemMetric],
    calculated_at: datetime | None = None,
) -> EmployeeScoreResult:
    engine = KPICalculationEngine(calculated_at=calculated_at)
    return engine.score_employee_from_models(
        employee,
        review_date=review_date,
        metrics_by_item_id=metrics_by_item_id,
    )


score_employee_kpis = calculate_employee_kpi_score
