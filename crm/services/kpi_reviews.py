import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMAuditLog
from crm.models_employee import EmployeeProfile
from crm.models_kpi import (
    KPIItemDefinition,
    KPISettings,
    KPITemplateVersion,
)
from crm.models_kpi_reviews import (
    KPIReview,
    KPIReviewItemEntry,
    KPIReviewTransition,
)
from crm.services.employee_profiles import employee_display_name
from crm.services.kpi_assignments import assignments_for_date
from crm.services.kpi_calculation_engine import (
    EmployeeCalculationInput,
    EmployeeRoleInput,
    KPIItemInput,
    KPIItemMetric,
    KPITemplateInput,
    KPICalculationEngine,
    MeasurementType,
    ScoreRanges,
    ScoringDirection,
)
from crm.services.kpi_review_permissions import (
    can_approve_kpi_review,
    can_create_kpi_review,
    can_lock_kpi_review,
    can_manage_kpi_review,
)


class KPIReviewWorkflowError(Exception):
    pass


PERCENT_MAX = Decimal("100")
MEASUREMENT_ALIASES = {
    "percentage": MeasurementType.PERCENTAGE,
    "count": MeasurementType.COUNT,
    "currency": MeasurementType.CURRENCY,
    "boolean": MeasurementType.BOOLEAN,
    "manual": MeasurementType.MANUAL_SCORE,
    "manual score": MeasurementType.MANUAL_SCORE,
    "manual_score": MeasurementType.MANUAL_SCORE,
    "duration": MeasurementType.DURATION,
    "decimal": MeasurementType.DECIMAL,
}
DIRECTION_ALIASES = {
    "higher": ScoringDirection.HIGHER_IS_BETTER,
    "higher_is_better": ScoringDirection.HIGHER_IS_BETTER,
    "lower": ScoringDirection.LOWER_IS_BETTER,
    "lower_is_better": ScoringDirection.LOWER_IS_BETTER,
    "exact": ScoringDirection.EXACT_TARGET,
    "exact_target": ScoringDirection.EXACT_TARGET,
}


def _json_digest(payload):
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_approved_snapshot(review):
    if not review.approved_snapshot or not review.snapshot_digest:
        return False
    return _json_digest(review.approved_snapshot) == review.snapshot_digest


def _parse_measurement_configuration(item):
    raw = " ".join((item.measurement_method or "").strip().lower().split())
    type_text, separator, direction_text = raw.partition(":")
    measurement_type = MEASUREMENT_ALIASES.get(type_text)
    if measurement_type is None:
        raise KPIReviewWorkflowError(
            f"{item.name} has an unsupported measurement method. "
            "Use percentage, count, currency, boolean, manual score, duration, "
            "or decimal."
        )
    if separator:
        direction = DIRECTION_ALIASES.get(direction_text.strip())
        if direction is None:
            raise KPIReviewWorkflowError(
                f"{item.name} has an unsupported scoring direction."
            )
    elif measurement_type in {
        MeasurementType.MANUAL_SCORE,
        MeasurementType.BOOLEAN,
    }:
        direction = ScoringDirection.EXACT_TARGET
    elif measurement_type == MeasurementType.DURATION:
        direction = ScoringDirection.LOWER_IS_BETTER
    else:
        direction = ScoringDirection.HIGHER_IS_BETTER

    target = (item.target or "").strip()
    if measurement_type == MeasurementType.MANUAL_SCORE:
        target = None
    elif measurement_type == MeasurementType.BOOLEAN:
        normalized_target = target.casefold()
        if not target:
            target = True
        elif normalized_target in {"true", "yes", "1"}:
            target = True
        elif normalized_target in {"false", "no", "0"}:
            target = False
        else:
            raise KPIReviewWorkflowError(
                f"{item.name} requires a true or false target."
            )
    elif target:
        try:
            target = str(Decimal(target))
        except InvalidOperation:
            raise KPIReviewWorkflowError(
                f"{item.name} requires a numeric target."
            ) from None
    elif measurement_type != MeasurementType.PERCENTAGE:
        raise KPIReviewWorkflowError(f"{item.name} requires a numeric target.")
    else:
        target = None

    return {
        "measurement_type": measurement_type.value,
        "direction": direction.value,
        "target": target,
        "minimum": "0",
        "maximum": (
            "100" if measurement_type == MeasurementType.PERCENTAGE else None
        ),
    }


def _score_ranges_snapshot(source):
    return {
        "red_min": str(source.red_min),
        "red_max": str(source.red_max),
        "yellow_min": str(source.yellow_min),
        "yellow_max": str(source.yellow_max),
        "green_min": str(source.green_min),
        "green_max": str(source.green_max),
    }


def _effective_template_versions(template_ids, review_date):
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
        .prefetch_related(
            Prefetch(
                "items",
                queryset=KPIItemDefinition.objects.order_by("sort_order", "id"),
            )
        )
        .order_by("template_id", "-version")
    )
    grouped = {}
    for version in versions:
        grouped.setdefault(version.template_id, []).append(version)
    selected = {}
    for template_id in template_ids:
        candidates = grouped.get(template_id, [])
        if len(candidates) != 1:
            raise KPIReviewWorkflowError(
                "Exactly one effective published or retired template version "
                f"is required for KPI template {template_id}; found "
                f"{len(candidates)}."
            )
        selected[template_id] = candidates[0]
    return selected


def _build_definition_snapshot(employee, review_date):
    assignments = list(assignments_for_date(employee, review_date))
    if not assignments:
        raise KPIReviewWorkflowError(
            "The employee has no KPI assignments for the review date."
        )
    total_weight = sum(
        (assignment.role_weight for assignment in assignments),
        Decimal("0"),
    )
    if total_weight != PERCENT_MAX:
        raise KPIReviewWorkflowError(
            "Effective KPI assignment weights must total exactly 100."
        )
    manager_ids = {
        assignment.manager_id
        for assignment in assignments
        if assignment.manager_id is not None
    }
    if len(manager_ids) > 1:
        raise KPIReviewWorkflowError(
            "All employee KPI roles must use one manager for a combined review."
        )
    settings = KPISettings.objects.filter(is_active=True).first()
    if settings is None:
        raise KPIReviewWorkflowError("Active KPI settings are required.")
    versions = _effective_template_versions(
        {assignment.kpi_template_id for assignment in assignments},
        review_date,
    )

    role_snapshots = []
    for assignment in assignments:
        version = versions[assignment.kpi_template_id]
        items = []
        for item in version.items.all():
            if not item.is_active:
                continue
            measurement = _parse_measurement_configuration(item)
            items.append(
                {
                    "kpi_item_id": item.pk,
                    "name": item.name,
                    "weight": str(item.weight),
                    "measurement": measurement,
                    "status_ranges": {
                        **_score_ranges_snapshot(item),
                        "source": "kpi_item_definition",
                        "source_version": version.version,
                    },
                    "data_source": item.data_source,
                    "evidence_required": item.evidence_required,
                    "critical_failure_rule": item.critical_failure_rule,
                }
            )
        if not items:
            raise KPIReviewWorkflowError(
                f"{version.template.name} has no active KPI items."
            )
        role_snapshots.append(
            {
                "assignment_id": assignment.pk,
                "assignment_version": assignment.assignment_version,
                "role_weight": str(assignment.role_weight),
                "start_date": assignment.start_date.isoformat(),
                "end_date": (
                    assignment.end_date.isoformat() if assignment.end_date else None
                ),
                "template_id": version.template_id,
                "template_name": version.template.name,
                "template_version": version.version,
                "manager_id": assignment.manager_id,
                "items": items,
            }
        )
    return {
        "employee_id": employee.pk,
        "employee_user_id": employee.user_id,
        "review_date": review_date.isoformat(),
        "settings": {
            **_score_ranges_snapshot(settings),
            "source": "kpi_settings",
            "source_version": settings.version,
        },
        "roles": role_snapshots,
    }, next(iter(manager_ids), None)


def _audit(review, actor, *, action_type, old_status, new_status):
    CRMAuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        module="kpi_performance",
        record_id=str(review.pk),
        record_label=(
            f"{review.employee.public_name} "
            f"{review.get_period_type_display()} {review.period_start:%Y-%m-%d}"
        )[:220],
        action_type=action_type,
        field_name="status",
        previous_value=old_status,
        new_value=new_status,
        target_url=reverse("kpi_review_detail", args=[review.pk]),
    )


def _transition(
    review,
    *,
    action,
    from_status,
    to_status,
    actor,
    comment="",
    snapshot_digest="",
):
    transition = KPIReviewTransition(
        review=review,
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor=actor if actor and actor.is_authenticated else None,
        comment=(comment or "").strip(),
        snapshot_digest=snapshot_digest,
    )
    transition.save(internal_create=True)


def create_kpi_review(
    *,
    employee,
    period_type,
    period_start,
    period_end,
    review_date,
    actor,
):
    employee_id = getattr(employee, "pk", employee)
    employee = (
        EmployeeProfile.objects.select_related(
            "user",
            "department_ref",
            "position_ref",
        )
        .filter(pk=employee_id)
        .first()
    )
    if employee is None:
        raise KPIReviewWorkflowError("The selected employee does not exist.")
    if period_type not in dict(KPIReview.PERIOD_CHOICES):
        raise KPIReviewWorkflowError("Select a valid review period.")
    if period_end < period_start or not period_start <= review_date <= period_end:
        raise KPIReviewWorkflowError(
            "Review dates must be ordered and the review date must be in the period."
        )
    if not can_create_kpi_review(actor, employee, review_date=review_date):
        raise KPIReviewWorkflowError(
            "You do not have permission to create this employee review."
        )

    with transaction.atomic():
        definition_snapshot, manager_id = _build_definition_snapshot(
            employee, review_date
        )
        if not can_create_kpi_review(actor, employee, review_date=review_date):
            raise KPIReviewWorkflowError(
                "You do not have permission to create this employee review."
            )
        review = KPIReview(
            employee=employee,
            manager_id=manager_id,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            review_date=review_date,
            definition_snapshot=definition_snapshot,
            created_by=actor,
        )
        try:
            review.save()
        except ValidationError as exc:
            raise KPIReviewWorkflowError("; ".join(exc.messages)) from exc

        assignments_by_id = {
            assignment.pk: assignment
            for assignment in assignments_for_date(employee, review_date)
        }
        item_ids = {
            item["kpi_item_id"]
            for role in definition_snapshot["roles"]
            for item in role["items"]
        }
        items_by_id = {
            item.pk: item
            for item in KPIItemDefinition.objects.filter(pk__in=item_ids).select_related(
                "template_version"
            )
        }
        for role in definition_snapshot["roles"]:
            for item in role["items"]:
                entry = KPIReviewItemEntry(
                    review=review,
                    assignment=assignments_by_id[role["assignment_id"]],
                    kpi_item=items_by_id[item["kpi_item_id"]],
                )
                entry.save(internal_create=True)
        _transition(
            review,
            action=KPIReviewTransition.ACTION_CREATED,
            from_status="",
            to_status=KPIReview.STATUS_DRAFT,
            actor=actor,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_CREATED,
            old_status="",
            new_status=KPIReview.STATUS_DRAFT,
        )
        return review


def _ranges_from_snapshot(payload):
    return ScoreRanges(
        red_min=Decimal(payload["red_min"]),
        red_max=Decimal(payload["red_max"]),
        yellow_min=Decimal(payload["yellow_min"]),
        yellow_max=Decimal(payload["yellow_max"]),
        green_min=Decimal(payload["green_min"]),
        green_max=Decimal(payload["green_max"]),
        source=payload["source"],
        source_version=int(payload["source_version"]),
    )


def _actual_for_metric(entry, measurement_type):
    if entry.actual_value is None:
        raise KPIReviewWorkflowError(
            f"A result is required for {entry.kpi_item.name}."
        )
    if measurement_type == MeasurementType.BOOLEAN:
        if entry.actual_value not in {Decimal("0"), Decimal("1")}:
            raise KPIReviewWorkflowError(
                f"{entry.kpi_item.name} requires 0 or 1."
            )
        return bool(entry.actual_value)
    return entry.actual_value


def _calculate_review_snapshot(review, *, calculated_at=None):
    definition = review.definition_snapshot
    entry_map = {
        (entry.assignment_id, entry.kpi_item_id): entry
        for entry in review.item_entries.select_related("kpi_item").all()
    }
    role_inputs = []
    for role in definition["roles"]:
        item_inputs = []
        for item in role["items"]:
            key = (role["assignment_id"], item["kpi_item_id"])
            entry = entry_map.get(key)
            if entry is None:
                raise KPIReviewWorkflowError(
                    f"Review entry is missing for {item['name']}."
                )
            measurement = item["measurement"]
            measurement_type = MeasurementType(measurement["measurement_type"])
            item_inputs.append(
                KPIItemInput(
                    kpi_id=item["kpi_item_id"],
                    name=item["name"],
                    weight=Decimal(item["weight"]),
                    metric=KPIItemMetric(
                        actual=_actual_for_metric(entry, measurement_type),
                        target=measurement["target"],
                        measurement_type=measurement_type,
                        direction=ScoringDirection(measurement["direction"]),
                        minimum=measurement["minimum"],
                        maximum=measurement["maximum"],
                        critical_red=entry.critical_red,
                        critical_reason=entry.critical_reason,
                        critical_trigger=entry.critical_trigger,
                    ),
                    template_id=role["template_id"],
                    template_version=int(role["template_version"]),
                    assignment_id=role["assignment_id"],
                    assignment_version=int(role["assignment_version"]),
                    employee_id=definition["employee_id"],
                    role_name=role["template_name"],
                    status_ranges=_ranges_from_snapshot(item["status_ranges"]),
                )
            )
        template = KPITemplateInput(
            template_id=role["template_id"],
            template_name=role["template_name"],
            template_version=int(role["template_version"]),
            items=tuple(item_inputs),
            assignment_id=role["assignment_id"],
            assignment_version=int(role["assignment_version"]),
            employee_id=definition["employee_id"],
        )
        role_inputs.append(
            EmployeeRoleInput(
                assignment_id=role["assignment_id"],
                assignment_version=int(role["assignment_version"]),
                employee_id=definition["employee_id"],
                role_weight=Decimal(role["role_weight"]),
                template=template,
                start_date=date.fromisoformat(role["start_date"]),
                end_date=(
                    date.fromisoformat(role["end_date"])
                    if role["end_date"]
                    else None
                ),
            )
        )
    engine = KPICalculationEngine(
        score_ranges=_ranges_from_snapshot(definition["settings"]),
        calculated_at=calculated_at,
    )
    return engine.score_historical_review(
        EmployeeCalculationInput(
            employee_id=definition["employee_id"],
            review_date=date.fromisoformat(definition["review_date"]),
            roles=tuple(role_inputs),
        )
    ).as_dict()


def calculate_review_snapshot(review, *, calculated_at=None):
    try:
        return _calculate_review_snapshot(review, calculated_at=calculated_at)
    except KPIReviewWorkflowError:
        raise
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            detail = "; ".join(exc.messages)
        else:
            detail = str(exc)
        raise KPIReviewWorkflowError(
            f"The KPI calculation engine rejected this review: {detail}"
        ) from exc


def _locked_review(review):
    review_id = getattr(review, "pk", review)
    resolved = (
        KPIReview.objects.select_for_update()
        .select_related(
            "employee__user",
            "employee__department_ref",
            "manager__employee_profile",
        )
        .filter(pk=review_id)
        .first()
    )
    if resolved is None:
        raise KPIReviewWorkflowError("The selected KPI review does not exist.")
    return resolved


def save_kpi_review_draft(
    review,
    *,
    actor,
    entry_values,
    manager_comment="",
):
    with transaction.atomic():
        review = _locked_review(review)
        if review.status != KPIReview.STATUS_DRAFT:
            raise KPIReviewWorkflowError("Only Draft reviews can be edited.")
        if not can_manage_kpi_review(actor, review):
            raise KPIReviewWorkflowError(
                "You do not have permission to edit this review."
            )
        entries = list(
            review.item_entries.select_related(
                "assignment",
                "kpi_item__template_version",
            )
        )
        values_by_id = {int(key): value for key, value in entry_values.items()}
        if set(values_by_id) - {entry.pk for entry in entries}:
            raise KPIReviewWorkflowError("An invalid KPI review entry was supplied.")
        for entry in entries:
            values = values_by_id.get(entry.pk)
            if values is None:
                continue
            entry.actual_value = values.get("actual_value")
            entry.critical_red = bool(values.get("critical_red", False))
            entry.critical_reason = (values.get("critical_reason") or "").strip()
            entry.critical_trigger = (values.get("critical_trigger") or "").strip()
            entry.item_comment = (values.get("item_comment") or "").strip()
            try:
                entry.save(service_authorized=True)
            except ValidationError as exc:
                raise KPIReviewWorkflowError("; ".join(exc.messages)) from exc
        review.manager_comment = (manager_comment or "").strip()
        if all(entry.actual_value is not None for entry in entries):
            review.calculation_snapshot = calculate_review_snapshot(review)
        else:
            review.calculation_snapshot = {}
        review.save(
            update_fields=[
                "manager_comment",
                "calculation_snapshot",
                "updated_at",
            ]
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_SAVED,
            from_status=KPIReview.STATUS_DRAFT,
            to_status=KPIReview.STATUS_DRAFT,
            actor=actor,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_UPDATED,
            old_status=KPIReview.STATUS_DRAFT,
            new_status=KPIReview.STATUS_DRAFT,
        )
        return review


def submit_kpi_review(review, *, actor):
    with transaction.atomic():
        review = _locked_review(review)
        if review.status != KPIReview.STATUS_DRAFT:
            raise KPIReviewWorkflowError("Only Draft reviews can be submitted.")
        if not can_manage_kpi_review(actor, review):
            raise KPIReviewWorkflowError(
                "You do not have permission to submit this review."
            )
        result_snapshot = calculate_review_snapshot(review)
        now = timezone.now()
        previous_status = review.status
        review.calculation_snapshot = result_snapshot
        review.status = KPIReview.STATUS_SUBMITTED
        review.submitted_by = actor
        review.submitted_at = now
        review.save(
            workflow_authorized=True,
            update_fields=[
                "calculation_snapshot",
                "status",
                "submitted_by",
                "submitted_at",
                "updated_at",
            ],
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_SUBMITTED,
            from_status=previous_status,
            to_status=review.status,
            actor=actor,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
            old_status=previous_status,
            new_status=review.status,
        )
        return review


def start_kpi_review(review, *, actor):
    with transaction.atomic():
        review = _locked_review(review)
        if review.status != KPIReview.STATUS_SUBMITTED:
            raise KPIReviewWorkflowError(
                "Only Submitted reviews can move Under Review."
            )
        if not can_approve_kpi_review(actor, review):
            raise KPIReviewWorkflowError(
                "You do not have permission to review this submission."
            )
        previous_status = review.status
        review.status = KPIReview.STATUS_UNDER_REVIEW
        review.review_started_by = actor
        review.review_started_at = timezone.now()
        review.save(
            workflow_authorized=True,
            update_fields=[
                "status",
                "review_started_by",
                "review_started_at",
                "updated_at",
            ],
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_REVIEW_STARTED,
            from_status=previous_status,
            to_status=review.status,
            actor=actor,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
            old_status=previous_status,
            new_status=review.status,
        )
        return review


def approve_kpi_review(review, *, actor, comment=""):
    with transaction.atomic():
        review = _locked_review(review)
        if review.status != KPIReview.STATUS_UNDER_REVIEW:
            raise KPIReviewWorkflowError(
                "Only reviews Under Review can be approved."
            )
        if not can_approve_kpi_review(actor, review):
            raise KPIReviewWorkflowError(
                "You do not have permission to approve this review."
            )
        if review.employee.user_id == actor.pk:
            raise KPIReviewWorkflowError("You cannot approve your own KPI review.")
        result_snapshot = calculate_review_snapshot(review)
        now = timezone.now()
        approval_comment = (comment or "").strip()
        entries = list(
            review.item_entries.select_related(
                "assignment",
                "kpi_item",
            )
        )
        approved_snapshot = {
            "result": result_snapshot,
            "definition": review.definition_snapshot,
            "entries": [
                {
                    "entry_id": entry.pk,
                    "assignment_id": entry.assignment_id,
                    "kpi_item_id": entry.kpi_item_id,
                    "actual_value": (
                        str(entry.actual_value)
                        if entry.actual_value is not None
                        else None
                    ),
                    "critical_red": entry.critical_red,
                    "critical_reason": entry.critical_reason,
                    "critical_trigger": entry.critical_trigger,
                    "item_comment": entry.item_comment,
                }
                for entry in entries
            ],
            "comments": {
                "manager": review.manager_comment,
                "approval": approval_comment,
            },
            "approval": {
                "approved_by_id": actor.pk,
                "approved_by": employee_display_name(actor),
                "approved_at": now.isoformat(),
            },
        }
        digest = _json_digest(approved_snapshot)
        previous_status = review.status
        review.status = KPIReview.STATUS_APPROVED
        review.approval_comment = approval_comment
        review.approved_by = actor
        review.approved_at = now
        review.approved_snapshot = approved_snapshot
        review.snapshot_digest = digest
        review.calculation_snapshot = result_snapshot
        review.save(
            workflow_authorized=True,
            update_fields=[
                "status",
                "approval_comment",
                "approved_by",
                "approved_at",
                "approved_snapshot",
                "snapshot_digest",
                "calculation_snapshot",
                "updated_at",
            ],
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_APPROVED,
            from_status=previous_status,
            to_status=review.status,
            actor=actor,
            comment=approval_comment,
            snapshot_digest=digest,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_APPROVED,
            old_status=previous_status,
            new_status=review.status,
        )
        return review


def reject_kpi_review(review, *, actor, comment):
    rejection_comment = (comment or "").strip()
    if not rejection_comment:
        raise KPIReviewWorkflowError("A rejection comment is required.")
    with transaction.atomic():
        review = _locked_review(review)
        if review.status != KPIReview.STATUS_UNDER_REVIEW:
            raise KPIReviewWorkflowError(
                "Only reviews Under Review can be rejected."
            )
        if not can_approve_kpi_review(actor, review):
            raise KPIReviewWorkflowError(
                "You do not have permission to reject this review."
            )
        previous_status = review.status
        review.status = KPIReview.STATUS_REJECTED
        review.rejection_comment = rejection_comment
        review.rejected_by = actor
        review.rejected_at = timezone.now()
        review.save(
            workflow_authorized=True,
            update_fields=[
                "status",
                "rejection_comment",
                "rejected_by",
                "rejected_at",
                "updated_at",
            ],
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_REJECTED,
            from_status=previous_status,
            to_status=review.status,
            actor=actor,
            comment=rejection_comment,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_REJECTED,
            old_status=previous_status,
            new_status=review.status,
        )

        rejected_status = review.status
        review.status = KPIReview.STATUS_DRAFT
        review.calculation_snapshot = {}
        review.review_started_by = None
        review.review_started_at = None
        review.save(
            workflow_authorized=True,
            update_fields=[
                "status",
                "calculation_snapshot",
                "review_started_by",
                "review_started_at",
                "updated_at",
            ],
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_RETURNED_TO_DRAFT,
            from_status=rejected_status,
            to_status=review.status,
            actor=actor,
            comment=rejection_comment,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
            old_status=rejected_status,
            new_status=review.status,
        )
        return review


def lock_kpi_review(review, *, actor):
    with transaction.atomic():
        review = _locked_review(review)
        if review.status != KPIReview.STATUS_APPROVED:
            raise KPIReviewWorkflowError("Only Approved reviews can be locked.")
        if not can_lock_kpi_review(actor, review):
            raise KPIReviewWorkflowError(
                "You do not have permission to lock this review."
            )
        if not verify_approved_snapshot(review):
            raise KPIReviewWorkflowError(
                "The approved review snapshot failed its integrity check."
            )
        previous_status = review.status
        review.status = KPIReview.STATUS_LOCKED
        review.locked_by = actor
        review.locked_at = timezone.now()
        review.save(
            workflow_authorized=True,
            update_fields=[
                "status",
                "locked_by",
                "locked_at",
                "updated_at",
            ],
        )
        _transition(
            review,
            action=KPIReviewTransition.ACTION_LOCKED,
            from_status=previous_status,
            to_status=review.status,
            actor=actor,
            snapshot_digest=review.snapshot_digest,
        )
        _audit(
            review,
            actor,
            action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
            old_status=previous_status,
            new_status=review.status,
        )
        return review
