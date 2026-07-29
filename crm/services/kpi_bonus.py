import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crm.models_employee import EmployeeProfile
from crm.models_kpi_assignments import EmployeeKPIRoleAssignmentHistory
from crm.models_kpi_bonus import (
    KPIBonusCalculation,
    KPIBonusRuleSet,
    KPIBonusWeightProfile,
)
from crm.models_kpi_reviews import KPIReview
from crm.services.kpi_reviews import verify_approved_snapshot


BONUS_CALCULATION_VERSION = "kpi-bonus-engine/1.0"
BONUS_FORMULA_VERSION = "kpi-bonus-formula/1.0"
SCORE_QUANTUM = Decimal("0.000001")
MONEY_QUANTUM = Decimal("0.01")
PERCENT = Decimal("100")

ELIGIBLE = "eligible"
NOT_ELIGIBLE = "not_eligible"
PENDING = "pending"
BLOCKED = "blocked"


class KPIBonusError(Exception):
    pass


def _decimal(value, field, *, required=True):
    if value is None:
        if required:
            raise KPIBonusError(f"{field} is required.")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KPIBonusError(f"{field} must be numeric.") from exc
    if not result.is_finite():
        raise KPIBonusError(f"{field} must be finite.")
    return result


def _score(value, field):
    result = _decimal(value, field)
    if result < 0 or result > PERCENT:
        raise KPIBonusError(f"{field} must be between 0 and 100.")
    return result


def _score_text(value):
    if value is None:
        return None
    return str(value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP))


def _money_text(value):
    if value is None:
        return None
    return str(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _digest(payload):
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovedBonusSource:
    review_id: int
    snapshot_digest: str
    employee_id: int
    review_date: str
    score: Decimal
    critical_red: bool
    calculation_version: str
    formula_version: str

    def as_dict(self):
        return {
            **asdict(self),
            "score": _score_text(self.score),
        }


@dataclass(frozen=True, slots=True)
class BonusEvaluation:
    eligibility_status: str
    eligible: bool
    reasons: tuple[str, ...]
    individual_score: Decimal | None
    team_score: Decimal | None
    company_score: Decimal | None
    individual_weight: Decimal
    team_weight: Decimal
    company_weight: Decimal
    individual_weighted_score: Decimal | None
    team_weighted_score: Decimal | None
    company_weighted_score: Decimal | None
    final_bonus_score: Decimal | None
    attendance_multiplier: Decimal
    base_bonus_amount: Decimal | None
    calculated_bonus_amount: Decimal | None
    final_bonus_amount: Decimal | None
    bonus_floor_applied: bool
    bonus_cap_applied: bool
    currency: str
    approval_required: bool
    critical_red: bool
    review_source: ApprovedBonusSource | None
    team_source: ApprovedBonusSource | None
    company_source: ApprovedBonusSource | None
    employee_id: int | None
    manager_id: int | None
    department_code: str
    department_name: str
    team_scope_code: str
    team_scope_name: str
    review_date: str | None
    formula_version: str
    bonus_rule_code: str
    bonus_rule_version: int
    weight_profile_code: str
    weight_profile_version: int
    calculation_version: str
    calculated_at: str

    def as_dict(self):
        return {
            "eligibility_status": self.eligibility_status,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "components": {
                "individual": {
                    "score": _score_text(self.individual_score),
                    "weight": _score_text(self.individual_weight),
                    "weighted_score": _score_text(
                        self.individual_weighted_score
                    ),
                },
                "team": {
                    "score": _score_text(self.team_score),
                    "weight": _score_text(self.team_weight),
                    "weighted_score": _score_text(self.team_weighted_score),
                },
                "company": {
                    "score": _score_text(self.company_score),
                    "weight": _score_text(self.company_weight),
                    "weighted_score": _score_text(
                        self.company_weighted_score
                    ),
                },
            },
            "final_bonus_score": _score_text(self.final_bonus_score),
            "attendance_multiplier": str(self.attendance_multiplier),
            "payout": {
                "base_amount": _money_text(self.base_bonus_amount),
                "calculated_amount": _money_text(
                    self.calculated_bonus_amount
                ),
                "final_amount": _money_text(self.final_bonus_amount),
                "floor_applied": self.bonus_floor_applied,
                "cap_applied": self.bonus_cap_applied,
                "currency": self.currency,
                "approval_required": self.approval_required,
                "payment_created": False,
            },
            "critical_red": self.critical_red,
            "sources": {
                "review": (
                    self.review_source.as_dict() if self.review_source else None
                ),
                "team": self.team_source.as_dict() if self.team_source else None,
                "company": (
                    self.company_source.as_dict()
                    if self.company_source
                    else None
                ),
            },
            "employee_id": self.employee_id,
            "manager_id": self.manager_id,
            "department": {
                "code": self.department_code,
                "name": self.department_name,
            },
            "team_scope": {
                "code": self.team_scope_code,
                "name": self.team_scope_name,
            },
            "review_date": self.review_date,
            "formula_version": self.formula_version,
            "bonus_rule": {
                "code": self.bonus_rule_code,
                "version": self.bonus_rule_version,
            },
            "weight_profile": {
                "code": self.weight_profile_code,
                "version": self.weight_profile_version,
            },
            "calculation_version": self.calculation_version,
            "calculated_at": self.calculated_at,
        }


def _effective_filter(queryset, as_of):
    return queryset.filter(effective_start__lte=as_of).filter(
        Q(effective_end__isnull=True) | Q(effective_end__gte=as_of)
    )


def effective_bonus_rule_set(as_of, *, code=None):
    queryset = KPIBonusRuleSet.objects.filter(
        status=KPIBonusRuleSet.STATUS_PUBLISHED,
        weight_profile__status=KPIBonusWeightProfile.STATUS_PUBLISHED,
    ).select_related("weight_profile")
    if code:
        queryset = queryset.filter(code=code)
    queryset = _effective_filter(queryset, as_of).filter(
        weight_profile__effective_start__lte=as_of,
    ).filter(
        Q(weight_profile__effective_end__isnull=True)
        | Q(weight_profile__effective_end__gte=as_of)
    )
    rows = list(queryset.order_by("code", "-version")[:2])
    if len(rows) != 1:
        raise KPIBonusError(
            "Exactly one effective published bonus rule set is required; "
            f"found {len(rows)}."
        )
    return rows[0]


def _resolve_rule_set(rule_set, review_date):
    if rule_set is None:
        return effective_bonus_rule_set(review_date)
    rule_id = getattr(rule_set, "pk", rule_set)
    resolved = (
        KPIBonusRuleSet.objects.select_related("weight_profile")
        .filter(pk=rule_id)
        .first()
    )
    if resolved is None:
        raise KPIBonusError("The selected bonus rule set does not exist.")
    if resolved.status != KPIBonusRuleSet.STATUS_PUBLISHED:
        raise KPIBonusError("The selected bonus rule set is not published.")
    if resolved.weight_profile.status != KPIBonusWeightProfile.STATUS_PUBLISHED:
        raise KPIBonusError("The selected bonus weight profile is not published.")
    for configured in (resolved, resolved.weight_profile):
        if configured.effective_start > review_date or (
            configured.effective_end and configured.effective_end < review_date
        ):
            raise KPIBonusError(
                "The selected bonus configuration is not effective for the review date."
            )
    return resolved


def _review_id(value):
    if value is None:
        return None
    review_id = getattr(value, "pk", value)
    if not review_id:
        raise KPIBonusError("A valid KPI review is required.")
    return int(review_id)


def _resolve_reviews(review, team_review, company_review):
    requested = {
        "review": _review_id(review),
        "team": _review_id(team_review),
        "company": _review_id(company_review),
    }
    ids = {review_id for review_id in requested.values() if review_id}
    rows = {
        row.pk: row
        for row in KPIReview.objects.filter(pk__in=ids).select_related(
            "employee__user",
            "employee__department_ref",
            "manager",
        )
    }
    missing = ids - set(rows)
    if missing:
        raise KPIBonusError("One or more selected KPI reviews do not exist.")
    return {
        key: rows.get(review_id)
        for key, review_id in requested.items()
    }


def _approved_source(review, label):
    if review is None:
        return None, PENDING, f"{label}_review_missing"
    if review.status not in {
        KPIReview.STATUS_APPROVED,
        KPIReview.STATUS_LOCKED,
    }:
        return None, PENDING, f"{label}_review_not_approved"
    if not verify_approved_snapshot(review):
        return None, BLOCKED, f"{label}_snapshot_invalid"
    result = review.approved_snapshot.get("result")
    if not isinstance(result, dict):
        return None, BLOCKED, f"{label}_snapshot_invalid"
    try:
        score = _score(result.get("score"), f"{label} score")
    except KPIBonusError:
        return None, BLOCKED, f"{label}_score_invalid"
    return (
        ApprovedBonusSource(
            review_id=review.pk,
            snapshot_digest=review.snapshot_digest,
            employee_id=review.employee_id,
            review_date=review.review_date.isoformat(),
            score=score,
            critical_red=bool(result.get("critical_red")),
            calculation_version=str(result.get("calculation_version") or ""),
            formula_version=str(result.get("formula_version") or ""),
        ),
        None,
        None,
    )


def _assignment_history(review):
    roles = review.approved_snapshot.get("definition", {}).get("roles", [])
    pairs = {
        (int(role["assignment_id"]), int(role["assignment_version"]))
        for role in roles
    }
    assignment_ids = {assignment_id for assignment_id, _version in pairs}
    rows = EmployeeKPIRoleAssignmentHistory.objects.filter(
        assignment_id__in=assignment_ids,
    )
    history = {
        (row.assignment_id, row.assignment_version): row.new_values
        for row in rows
        if (row.assignment_id, row.assignment_version) in pairs
    }
    return roles, history


def _individual_bonus_score(review):
    roles, history = _assignment_history(review)
    if not roles:
        return None, BLOCKED, ("assignment_snapshot_missing",)

    review_date = review.review_date
    eligible_ids = set()
    reasons = []
    for role in roles:
        key = (int(role["assignment_id"]), int(role["assignment_version"]))
        values = history.get(key)
        if not values:
            return None, BLOCKED, ("assignment_history_missing",)
        start = date.fromisoformat(values["start_date"])
        end = (
            date.fromisoformat(values["end_date"])
            if values.get("end_date")
            else None
        )
        active_for_review = (
            values.get("is_active") is True
            and values.get("is_archived") is False
            and start <= review_date
            and (end is None or end >= review_date)
        )
        if not active_for_review:
            reasons.append("assignment_inactive")
            continue
        if values.get("bonus_eligible") is True:
            eligible_ids.add(key[0])

    if reasons:
        return None, NOT_ELIGIBLE, tuple(sorted(set(reasons)))
    if not eligible_ids:
        return None, NOT_ELIGIBLE, ("bonus_ineligible_assignment",)

    result_roles = review.approved_snapshot.get("result", {}).get("roles", [])
    matching = [
        role
        for role in result_roles
        if int(role.get("assignment_id")) in eligible_ids
    ]
    if len(matching) != len(eligible_ids):
        return None, BLOCKED, ("assignment_result_missing",)
    try:
        eligible_weight = sum(
            (_score(role["weight"], "role weight") for role in matching),
            Decimal("0"),
        )
        weighted_score = sum(
            (
                _score(role["weighted_score"], "role weighted score")
                for role in matching
            ),
            Decimal("0"),
        )
    except (KeyError, TypeError, ValueError, KPIBonusError):
        return None, BLOCKED, ("assignment_result_invalid",)
    if eligible_weight <= 0:
        return None, BLOCKED, ("assignment_weight_invalid",)
    score = (weighted_score * PERCENT / eligible_weight).quantize(
        SCORE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if score < 0 or score > PERCENT:
        return None, BLOCKED, ("individual_score_invalid",)
    return score, None, ()


def _department_snapshot(employee):
    if employee.department_ref_id:
        return employee.department_ref.code, employee.department_ref.name
    code = employee.department or ""
    labels = dict(EmployeeProfile.DEPARTMENT_CHOICES)
    return code, labels.get(code, code.replace("_", " ").title())


def _empty_evaluation(
    *,
    status,
    reasons,
    rule_set,
    review,
    calculated_at,
    source=None,
    team_source=None,
    company_source=None,
    critical_red=False,
):
    profile = rule_set.weight_profile
    employee = review.employee if review else None
    department_code, department_name = (
        _department_snapshot(employee) if employee else ("", "")
    )
    return BonusEvaluation(
        eligibility_status=status,
        eligible=False,
        reasons=tuple(dict.fromkeys(reasons)),
        individual_score=None,
        team_score=team_source.score if team_source else None,
        company_score=company_source.score if company_source else None,
        individual_weight=profile.individual_weight,
        team_weight=profile.team_weight,
        company_weight=profile.company_weight,
        individual_weighted_score=None,
        team_weighted_score=None,
        company_weighted_score=None,
        final_bonus_score=None,
        attendance_multiplier=rule_set.attendance_multiplier_default,
        base_bonus_amount=None,
        calculated_bonus_amount=None,
        final_bonus_amount=None,
        bonus_floor_applied=False,
        bonus_cap_applied=False,
        currency=rule_set.currency,
        approval_required=rule_set.approval_required,
        critical_red=critical_red,
        review_source=source,
        team_source=team_source,
        company_source=company_source,
        employee_id=employee.pk if employee else None,
        manager_id=review.manager_id if review else None,
        department_code=department_code,
        department_name=department_name,
        team_scope_code=profile.team_scope_code,
        team_scope_name=profile.team_scope_name,
        review_date=review.review_date.isoformat() if review else None,
        formula_version=BONUS_FORMULA_VERSION,
        bonus_rule_code=rule_set.code,
        bonus_rule_version=rule_set.version,
        weight_profile_code=profile.code,
        weight_profile_version=profile.version,
        calculation_version=BONUS_CALCULATION_VERSION,
        calculated_at=calculated_at.isoformat(),
    )


def evaluate_bonus(
    review,
    *,
    rule_set=None,
    team_review=None,
    company_review=None,
    attendance_multiplier=None,
    base_bonus_amount=None,
    calculated_at=None,
):
    calculated_at = calculated_at or timezone.now()
    reviews = _resolve_reviews(review, team_review, company_review)
    primary = reviews["review"]
    review_date = primary.review_date if primary else timezone.localdate()
    rule_set = _resolve_rule_set(rule_set, review_date)
    profile = rule_set.weight_profile

    source, source_status, source_reason = _approved_source(primary, "review")
    if source_status:
        return _empty_evaluation(
            status=source_status,
            reasons=(source_reason,),
            rule_set=rule_set,
            review=primary,
            calculated_at=calculated_at,
        )

    team_source = None
    company_source = None
    source_errors = []
    if profile.team_weight > 0:
        team_source, status, reason = _approved_source(reviews["team"], "team")
        if status:
            source_errors.append((status, reason))
    if profile.company_weight > 0:
        company_source, status, reason = _approved_source(
            reviews["company"],
            "company",
        )
        if status:
            source_errors.append((status, reason))
    if source_errors:
        status = BLOCKED if any(row[0] == BLOCKED for row in source_errors) else PENDING
        return _empty_evaluation(
            status=status,
            reasons=tuple(row[1] for row in source_errors),
            rule_set=rule_set,
            review=primary,
            calculated_at=calculated_at,
            source=source,
            team_source=team_source,
            company_source=company_source,
        )

    individual_score, assignment_status, assignment_reasons = (
        _individual_bonus_score(primary)
    )
    if assignment_status:
        return _empty_evaluation(
            status=assignment_status,
            reasons=assignment_reasons,
            rule_set=rule_set,
            review=primary,
            calculated_at=calculated_at,
            source=source,
            team_source=team_source,
            company_source=company_source,
            critical_red=source.critical_red,
        )

    if not rule_set.bonus_enabled:
        return _empty_evaluation(
            status=BLOCKED,
            reasons=("bonus_disabled",),
            rule_set=rule_set,
            review=primary,
            calculated_at=calculated_at,
            source=source,
            team_source=team_source,
            company_source=company_source,
        )
    if (
        primary.employee.is_archived
        or primary.employee.status not in rule_set.eligible_employee_statuses
    ):
        return _empty_evaluation(
            status=NOT_ELIGIBLE,
            reasons=("employee_inactive",),
            rule_set=rule_set,
            review=primary,
            calculated_at=calculated_at,
            source=source,
            team_source=team_source,
            company_source=company_source,
        )

    critical_red = any(
        candidate and candidate.critical_red
        for candidate in (source, team_source, company_source)
    )
    if critical_red and rule_set.critical_red_behavior != KPIBonusRuleSet.CRITICAL_ALLOW:
        status = (
            BLOCKED
            if rule_set.critical_red_behavior == KPIBonusRuleSet.CRITICAL_BLOCK
            else NOT_ELIGIBLE
        )
        return _empty_evaluation(
            status=status,
            reasons=("critical_red",),
            rule_set=rule_set,
            review=primary,
            calculated_at=calculated_at,
            source=source,
            team_source=team_source,
            company_source=company_source,
            critical_red=True,
        )

    multiplier = (
        _decimal(attendance_multiplier, "attendance multiplier")
        if attendance_multiplier is not None
        else rule_set.attendance_multiplier_default
    )
    if not (
        rule_set.attendance_multiplier_min
        <= multiplier
        <= rule_set.attendance_multiplier_max
    ):
        raise KPIBonusError(
            "Attendance multiplier is outside the configured rule range."
        )
    base_amount = _decimal(
        base_bonus_amount,
        "base bonus amount",
        required=False,
    )
    if base_amount is not None and base_amount < 0:
        raise KPIBonusError("Base bonus amount cannot be negative.")

    team_score = team_source.score if team_source else Decimal("0")
    company_score = company_source.score if company_source else Decimal("0")
    individual_weighted = (
        individual_score * profile.individual_weight / PERCENT
    )
    team_weighted = team_score * profile.team_weight / PERCENT
    company_weighted = company_score * profile.company_weight / PERCENT
    final_score = (
        individual_weighted + team_weighted + company_weighted
    ).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)

    status = ELIGIBLE
    reasons = []
    if final_score < rule_set.minimum_score:
        status = NOT_ELIGIBLE
        reasons.append("insufficient_score")
    if critical_red:
        reasons.append("critical_red_allowed")

    calculated_amount = None
    final_amount = None
    floor_applied = False
    cap_applied = False
    if base_amount is not None:
        calculated_amount = (
            base_amount * final_score / PERCENT * multiplier
        ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        final_amount = calculated_amount if status == ELIGIBLE else Decimal("0")
        if status == ELIGIBLE and final_amount < rule_set.bonus_floor:
            final_amount = rule_set.bonus_floor
            floor_applied = True
        if (
            status == ELIGIBLE
            and rule_set.bonus_cap is not None
            and final_amount > rule_set.bonus_cap
        ):
            final_amount = rule_set.bonus_cap
            cap_applied = True

    department_code, department_name = _department_snapshot(primary.employee)
    return BonusEvaluation(
        eligibility_status=status,
        eligible=status == ELIGIBLE,
        reasons=tuple(reasons),
        individual_score=individual_score,
        team_score=team_source.score if team_source else None,
        company_score=company_source.score if company_source else None,
        individual_weight=profile.individual_weight,
        team_weight=profile.team_weight,
        company_weight=profile.company_weight,
        individual_weighted_score=individual_weighted,
        team_weighted_score=team_weighted,
        company_weighted_score=company_weighted,
        final_bonus_score=final_score,
        attendance_multiplier=multiplier,
        base_bonus_amount=base_amount,
        calculated_bonus_amount=calculated_amount,
        final_bonus_amount=final_amount,
        bonus_floor_applied=floor_applied,
        bonus_cap_applied=cap_applied,
        currency=rule_set.currency,
        approval_required=rule_set.approval_required,
        critical_red=critical_red,
        review_source=source,
        team_source=team_source,
        company_source=company_source,
        employee_id=primary.employee_id,
        manager_id=primary.manager_id,
        department_code=department_code,
        department_name=department_name,
        team_scope_code=profile.team_scope_code,
        team_scope_name=profile.team_scope_name,
        review_date=primary.review_date.isoformat(),
        formula_version=BONUS_FORMULA_VERSION,
        bonus_rule_code=rule_set.code,
        bonus_rule_version=rule_set.version,
        weight_profile_code=profile.code,
        weight_profile_version=profile.version,
        calculation_version=BONUS_CALCULATION_VERSION,
        calculated_at=calculated_at.isoformat(),
    )


def verify_bonus_calculation(calculation):
    return bool(
        calculation.result_snapshot
        and calculation.result_digest
        and _digest(calculation.result_snapshot) == calculation.result_digest
    )


def create_bonus_calculation(
    review,
    *,
    rule_set=None,
    team_review=None,
    company_review=None,
    attendance_multiplier=None,
    base_bonus_amount=None,
    actor=None,
    calculated_at=None,
):
    review_id = _review_id(review)
    if base_bonus_amount is None:
        raise KPIBonusError(
            "Base bonus amount is required for a stored bonus calculation."
        )
    with transaction.atomic():
        existing = (
            KPIBonusCalculation.objects.select_for_update()
            .filter(review_id=review_id)
            .first()
        )
        if existing:
            if not verify_bonus_calculation(existing):
                raise KPIBonusError(
                    "The existing historical bonus snapshot failed verification."
                )
            return existing

        evaluation = evaluate_bonus(
            review_id,
            rule_set=rule_set,
            team_review=team_review,
            company_review=company_review,
            attendance_multiplier=attendance_multiplier,
            base_bonus_amount=base_bonus_amount,
            calculated_at=calculated_at,
        )
        if evaluation.eligibility_status == PENDING:
            raise KPIBonusError(
                "Pending bonus evaluations cannot be stored as final calculations."
            )
        payload = evaluation.as_dict()
        resolved_review = KPIReview.objects.select_related(
            "employee",
            "manager",
        ).get(pk=review_id)
        resolved_rule = _resolve_rule_set(
            rule_set,
            resolved_review.review_date,
        )
        calculation = KPIBonusCalculation(
            review=resolved_review,
            team_review_id=(
                evaluation.team_source.review_id
                if evaluation.team_source
                else None
            ),
            company_review_id=(
                evaluation.company_source.review_id
                if evaluation.company_source
                else None
            ),
            rule_set=resolved_rule,
            weight_profile=resolved_rule.weight_profile,
            employee=resolved_review.employee,
            manager=resolved_review.manager,
            department_code=evaluation.department_code,
            department_name=evaluation.department_name,
            team_scope_code=evaluation.team_scope_code,
            team_scope_name=evaluation.team_scope_name,
            review_date=resolved_review.review_date,
            eligibility_status=evaluation.eligibility_status,
            reason_codes=list(evaluation.reasons),
            formula_version=BONUS_FORMULA_VERSION,
            bonus_rule_version=resolved_rule.version,
            calculation_version=BONUS_CALCULATION_VERSION,
            review_snapshot_digest=resolved_review.snapshot_digest,
            result_snapshot=payload,
            result_digest=_digest(payload),
            calculated_by=actor,
            calculated_at=datetime.fromisoformat(evaluation.calculated_at),
        )
        try:
            calculation.save(internal_create=True)
        except ValidationError as exc:
            raise KPIBonusError("; ".join(exc.messages)) from exc
        return calculation
