from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crm.models import ExchangeRate, FactoryRunningCostDefault, QuickCosting, QuickCostingTimelineSnapshot
from crm.services.costing_currency import CurrencyConversionError, convert_currency, normalize_costing_currency
from crm.services.financial_currency import money


class FactoryTimelineError(ValueError):
    pass


_UNSET = object()


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise FactoryTimelineError("An authenticated actor is required.")
    return actor


def _pricing_type(quick_costing):
    if quick_costing.is_sampling:
        return "SAMPLE"
    return {
        QuickCosting.PRICING_FOB: "FOB",
        QuickCosting.PRICING_CMT: "CMT",
        QuickCosting.PRICING_FULL_PACKAGE: "FULL_PACKAGE",
        "door_to_door": "DOOR_TO_DOOR",
    }.get(quick_costing.effective_pricing_type, "OTHER")


def configured_factory_default(quick_costing, *, as_of=None):
    """Return the current Bangladesh factory rate maintained in Finance settings."""
    effective_date = as_of or timezone.localdate()
    queryset = FactoryRunningCostDefault.objects.filter(
        is_active=True,
        side="BD",
        effective_from__lte=effective_date,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=effective_date))
    target_currency = normalize_costing_currency(quick_costing.currency)
    matching = queryset.filter(currency=target_currency).order_by("-effective_from", "-pk").first()
    if matching:
        return matching
    return queryset.order_by("-effective_from", "-pk").first()


def timeline_required_for_approval(quick_costing):
    """Preserve historical costings while requiring the configured timeline for new approvals."""
    daily_default = configured_factory_default(quick_costing)
    if not daily_default:
        return False
    return not quick_costing.created_at or quick_costing.created_at >= daily_default.created_at


def _timeline_cost_in_costing_currency(quick_costing, amount, source_currency):
    target_currency = normalize_costing_currency(quick_costing.currency)
    source_currency = normalize_costing_currency(source_currency)
    if source_currency == target_currency:
        return money(amount)
    bdt_per_cad = quick_costing.exchange_rate_bdt_per_cad
    opportunity = getattr(quick_costing, "opportunity", None)
    bdt_per_usd = getattr(opportunity, "fx_rate_bdt_per_usd", None) if opportunity else None
    cad_per_usd = None
    if bdt_per_cad and bdt_per_usd and bdt_per_cad > 0:
        cad_per_usd = bdt_per_usd / bdt_per_cad
    try:
        return money(
            convert_currency(
                amount,
                source_currency,
                target_currency,
                bdt_per_cad=bdt_per_cad,
                bdt_per_usd=bdt_per_usd,
                cad_per_usd=cad_per_usd,
            )
        )
    except CurrencyConversionError as exc:
        raise FactoryTimelineError(
            f"Factory timeline conversion from {source_currency} to {target_currency} requires an approved stored exchange rate."
        ) from exc


def factory_cost_in_costing_currency(quick_costing, amount, source_currency):
    return _timeline_cost_in_costing_currency(quick_costing, amount, source_currency)


def _snapshot_finance_bdt_per_cad(quick_costing, source_currency):
    """Persist the approved Finance rate when a CAD costing has no stored rate."""
    target_currency = normalize_costing_currency(quick_costing.currency)
    source_currency = normalize_costing_currency(source_currency)
    if {source_currency, target_currency} != {"BDT", "CAD"}:
        return
    current_rate = quick_costing.exchange_rate_bdt_per_cad
    if current_rate is not None and current_rate > 1:
        return
    rate_row = ExchangeRate.objects.select_for_update().order_by("-updated_at", "-pk").first()
    rate = getattr(rate_row, "cad_to_bdt", None)
    if rate is None or rate <= 1:
        raise FactoryTimelineError(
            "A valid Finance BDT-per-CAD exchange rate is required before the factory timeline can be saved."
        )
    QuickCosting.objects.filter(pk=quick_costing.pk).update(exchange_rate_bdt_per_cad=rate)
    quick_costing.exchange_rate_bdt_per_cad = rate


def current_estimated_inputs(quick_costing):
    summary = quick_costing.calculation_summary()
    return {
        "estimated_revenue": money(summary.get("sales_value")),
        "other_estimated_cost": money(summary.get("total_cost")) + money(summary.get("commission_total")),
        "target_margin_percent": quick_costing.target_margin_percent,
    }


def apply_factory_timeline_to_summary(quick_costing, summary, *, snapshot=_UNSET):
    """Add the timeline overhead once to an operational Quick Costing summary."""
    if snapshot is _UNSET:
        snapshot = getattr(quick_costing, "factory_timeline", None)
    if not snapshot:
        return summary
    adjusted = dict(summary)
    quantity = Decimal(adjusted.get("quantity") or 0)
    revenue = money(adjusted.get("sales_value"))
    if snapshot.locked_at:
        estimated_profit = money(snapshot.estimated_profit)
        timeline_cost = money(adjusted.get("final_profit_after_commission")) - estimated_profit
    else:
        timeline_cost = _timeline_cost_in_costing_currency(
            quick_costing,
            snapshot.estimated_timeline_cost,
            snapshot.daily_cost_currency,
        )
        estimated_profit = money(adjusted.get("final_profit_after_commission")) - timeline_cost
    adjusted["factory_timeline_cost"] = timeline_cost
    adjusted["factory_timeline_cost_source_currency"] = snapshot.daily_cost_currency
    adjusted["factory_timeline_cost_source_amount"] = snapshot.estimated_timeline_cost
    adjusted["total_cost"] = money(adjusted.get("total_cost")) + timeline_cost
    adjusted["cost_per_piece"] = adjusted["total_cost"] / quantity if quantity else Decimal("0")
    adjusted["final_profit_after_commission"] = estimated_profit
    adjusted["final_profit_after_commission_per_piece"] = estimated_profit / quantity if quantity else Decimal("0")
    adjusted["net_profit_total"] = estimated_profit
    adjusted["net_profit_per_piece"] = estimated_profit / quantity if quantity else Decimal("0")
    adjusted["total_profit"] = estimated_profit
    adjusted["profit_per_piece"] = estimated_profit / quantity if quantity else Decimal("0")
    adjusted["net_profit_margin_percent"] = (
        estimated_profit / revenue * Decimal("100") if revenue else Decimal("0")
    )
    adjusted["profit_margin_percent"] = adjusted["net_profit_margin_percent"]
    target = adjusted.get("target_margin_percent")
    if target is None:
        adjusted["margin_status"] = "No target set"
    elif adjusted["net_profit_margin_percent"] >= target:
        adjusted["margin_status"] = "Meets target"
    else:
        adjusted["margin_status"] = "Below target"
    return adjusted


def _status(snapshot, actual_revenue):
    if snapshot.actual_production_days is None:
        return QuickCostingTimelineSnapshot.STATUS_GREEN
    minimum = snapshot.approved_minimum_margin_percent
    target = snapshot.target_margin_percent
    delay = snapshot.actual_production_days - snapshot.estimated_production_days
    small_delay_limit = max(1, int(snapshot.estimated_production_days * 0.10))
    actual_margin = None
    if snapshot.actual_profit is not None and actual_revenue is not None:
        actual_margin = (snapshot.actual_profit / actual_revenue * Decimal("100")) if actual_revenue > 0 else Decimal("-100")
    if (
        (snapshot.actual_profit is not None and snapshot.actual_profit < 0)
        or (minimum is not None and actual_margin is not None and actual_margin < minimum)
        or delay > small_delay_limit
    ):
        return QuickCostingTimelineSnapshot.STATUS_RED
    if delay > 0 or (target is not None and actual_margin is not None and actual_margin < target):
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
    _snapshot_finance_bdt_per_cad(locked, default.currency)
    estimated_days = int(estimated_days)
    if estimated_days <= 0:
        raise FactoryTimelineError("Estimated production days must be greater than zero.")
    daily_amount = money(daily_amount_snapshot if daily_amount_snapshot is not None else default.daily_amount)
    if daily_amount <= 0:
        raise FactoryTimelineError("The saved daily factory rate must be greater than zero.")
    timeline_cost = money(Decimal(estimated_days) * daily_amount)
    timeline_cost_in_costing_currency = _timeline_cost_in_costing_currency(locked, timeline_cost, default.currency)
    estimated_profit = money(estimated_revenue) - money(other_estimated_cost) - timeline_cost_in_costing_currency
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


def refresh_estimated_factory_timeline(quick_costing, snapshot, *, actor):
    if not snapshot or snapshot.locked_at:
        return snapshot
    inputs = current_estimated_inputs(quick_costing)
    return save_estimated_factory_timeline(
        quick_costing,
        estimated_days=snapshot.estimated_production_days,
        daily_default=snapshot.source_default,
        actor=actor,
        approved_minimum_margin_percent=snapshot.approved_minimum_margin_percent,
        daily_amount_snapshot=snapshot.daily_factory_cost,
        **inputs,
    )


@transaction.atomic
def record_actual_factory_timeline(
    snapshot,
    *,
    actual_days,
    actual_revenue=None,
    other_actual_cost=None,
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
    actual_profit = None
    normalized_actual_revenue = None
    if actual_revenue is not None or other_actual_cost is not None:
        if actual_revenue is None or other_actual_cost is None:
            raise FactoryTimelineError("Actual revenue and actual costs must both be available before actual profit is calculated.")
        normalized_actual_revenue = money(actual_revenue)
        actual_timeline_cost_in_costing_currency = _timeline_cost_in_costing_currency(
            locked.quick_costing,
            actual_timeline_cost,
            locked.daily_cost_currency,
        )
        actual_profit = normalized_actual_revenue - money(other_actual_cost) - actual_timeline_cost_in_costing_currency
    locked.actual_production_days = actual_days
    locked.actual_timeline_cost = actual_timeline_cost
    locked.actual_profit = actual_profit
    locked.modified_by = user
    locked.status = _status(locked, normalized_actual_revenue)
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
