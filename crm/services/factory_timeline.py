from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from crm.models import FactoryRunningCostDefault, QuickCosting, QuickCostingTimelineSnapshot
from crm.services.financial_currency import money


class FactoryTimelineError(ValueError):
    pass


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise FactoryTimelineError("An authenticated actor is required.")
    return actor


def _pricing_type(quick_costing):
    if quick_costing.costing_purpose == QuickCosting.PURPOSE_SAMPLE:
        return "SAMPLE"
    return {
        QuickCosting.PRICING_FOB: "FOB",
        QuickCosting.PRICING_CMT: "CMT",
        QuickCosting.PRICING_FULL_PACKAGE: "FULL_PACKAGE",
        "door_to_door": "DOOR_TO_DOOR",
    }.get(quick_costing.effective_pricing_type, "OTHER")


def _status(snapshot, actual_revenue):
    if snapshot.actual_production_days is None or snapshot.actual_profit is None:
        return QuickCostingTimelineSnapshot.STATUS_GREEN
    actual_margin = (snapshot.actual_profit / actual_revenue * Decimal("100")) if actual_revenue > 0 else Decimal("-100")
    minimum = snapshot.approved_minimum_margin_percent
    target = snapshot.target_margin_percent
    delay = snapshot.actual_production_days - snapshot.estimated_production_days
    small_delay_limit = max(1, int(snapshot.estimated_production_days * 0.10))
    if snapshot.actual_profit < 0 or (minimum is not None and actual_margin < minimum) or delay > small_delay_limit:
        return QuickCostingTimelineSnapshot.STATUS_RED
    if delay > 0 or (target is not None and actual_margin < target):
        return QuickCostingTimelineSnapshot.STATUS_YELLOW
    return QuickCostingTimelineSnapshot.STATUS_GREEN


@transaction.atomic
def lock_factory_timeline_for_approval(quick_costing, *, actor):
    user = _actor(actor)
    snapshot = QuickCostingTimelineSnapshot.objects.select_for_update().filter(quick_costing=quick_costing).first()
    if not snapshot or snapshot.locked_at:
        return snapshot
    approved_at = quick_costing.approved_at or timezone.now()
    QuickCostingTimelineSnapshot.objects.filter(pk=snapshot.pk).update(
        locked_at=approved_at,
        approved_by=quick_costing.approved_by or user,
        approved_at=approved_at,
        modified_by=user,
        modified_at=timezone.now(),
    )
    snapshot.refresh_from_db()
    return snapshot


@transaction.atomic
def save_estimated_factory_timeline(
    quick_costing,
    *,
    estimated_days,
    daily_default,
    estimated_revenue,
    other_estimated_cost,
    actor,
    target_margin_percent=None,
    approved_minimum_margin_percent=None,
    daily_amount_snapshot=None,
):
    user = _actor(actor)
    locked = QuickCosting.objects.select_for_update().get(pk=quick_costing.pk)
    default = FactoryRunningCostDefault.objects.select_for_update().get(pk=daily_default.pk, is_active=True)
    if (locked.currency or "").upper() != default.currency:
        raise FactoryTimelineError(
            "Quick Costing and daily factory rate currencies differ; provide an approved converted daily-rate default first."
        )
    estimated_days = int(estimated_days)
    if estimated_days <= 0:
        raise FactoryTimelineError("Estimated production days must be greater than zero.")
    daily_amount = money(daily_amount_snapshot if daily_amount_snapshot is not None else default.daily_amount)
    if daily_amount <= 0:
        raise FactoryTimelineError("The saved daily factory rate must be greater than zero.")
    timeline_cost = money(Decimal(estimated_days) * daily_amount)
    estimated_profit = money(estimated_revenue) - money(other_estimated_cost) - timeline_cost
    snapshot, created = QuickCostingTimelineSnapshot.objects.get_or_create(
        quick_costing=locked,
        defaults={
            "pricing_type": _pricing_type(locked),
            "estimated_production_days": estimated_days,
            "daily_factory_cost": daily_amount,
            "daily_cost_currency": default.currency,
            "estimated_timeline_cost": timeline_cost,
            "estimated_profit": estimated_profit,
            "target_margin_percent": target_margin_percent,
            "approved_minimum_margin_percent": approved_minimum_margin_percent,
            "source_default": default,
            "created_by": user,
            "modified_by": user,
        },
    )
    if not created:
        if snapshot.locked_at:
            raise FactoryTimelineError("The approved factory timeline estimate is locked; create a costing revision.")
        snapshot.pricing_type = _pricing_type(locked)
        snapshot.estimated_production_days = estimated_days
        snapshot.daily_factory_cost = daily_amount
        snapshot.daily_cost_currency = default.currency
        snapshot.estimated_timeline_cost = timeline_cost
        snapshot.estimated_profit = estimated_profit
        snapshot.target_margin_percent = target_margin_percent
        snapshot.approved_minimum_margin_percent = approved_minimum_margin_percent
        snapshot.source_default = default
        snapshot.modified_by = user
        snapshot.save()
    if locked.status in QuickCosting.ACTIVE_APPROVED_STATUSES and not snapshot.locked_at:
        QuickCostingTimelineSnapshot.objects.filter(pk=snapshot.pk).update(
            locked_at=locked.approved_at or timezone.now(),
            approved_by=locked.approved_by or user,
            approved_at=locked.approved_at or timezone.now(),
        )
        snapshot.refresh_from_db()
    return snapshot


@transaction.atomic
def record_actual_factory_timeline(
    snapshot,
    *,
    actual_days,
    actual_revenue,
    other_actual_cost,
    actor,
):
    user = _actor(actor)
    locked = QuickCostingTimelineSnapshot.objects.select_for_update().get(pk=snapshot.pk)
    if not locked.locked_at:
        raise FactoryTimelineError("Actual timeline can be recorded only after the estimate is approved and locked.")
    actual_days = int(actual_days)
    if actual_days <= 0:
        raise FactoryTimelineError("Actual production days must be greater than zero.")
    actual_timeline_cost = money(Decimal(actual_days) * locked.daily_factory_cost)
    actual_profit = money(actual_revenue) - money(other_actual_cost) - actual_timeline_cost
    locked.actual_production_days = actual_days
    locked.actual_timeline_cost = actual_timeline_cost
    locked.actual_profit = actual_profit
    locked.modified_by = user
    locked.status = _status(locked, money(actual_revenue))
    locked.save(
        update_fields=(
            "actual_production_days",
            "actual_timeline_cost",
            "actual_profit",
            "status",
            "modified_by",
            "modified_at",
        )
    )
    return locked
