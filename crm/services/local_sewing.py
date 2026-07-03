from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from crm.models import ProductionOrder
from crm.services.costing_currency import currency_summary_rows


MONEY = Decimal("0.01")
PERCENT = Decimal("0.01")

LOCAL_ORDER_TYPE = "sewing_charge"
LOCAL_FACTORY = "bd"
CANADA_EXPORT_ORDER_TYPES = ("fob", "canada_full")
COMPLETED_STATUSES = ("done", "closed_won")
CANCELLED_STATUSES = ("closed_lost",)
COMPLETED_OPERATIONAL_STATUSES = ("shipped",)
CANCELLED_OPERATIONAL_STATUSES = ("cancelled",)


def _decimal(value):
    if value in (None, ""):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value):
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def is_bangladesh_local_sewing(order):
    return bool(
        order
        and getattr(order, "order_type", "") == LOCAL_ORDER_TYPE
        and getattr(order, "factory_location", "") == LOCAL_FACTORY
    )


def local_sewing_queryset(queryset=None):
    queryset = queryset if queryset is not None else ProductionOrder.objects.all()
    return queryset.filter(
        is_archived=False,
        order_type=LOCAL_ORDER_TYPE,
        factory_location=LOCAL_FACTORY,
    )


def _sewing_stage(order, supplied_stages=None):
    stages = supplied_stages
    if stages is None:
        prefetched = getattr(order, "_prefetched_objects_cache", {}).get("stages")
        stages = prefetched if prefetched is not None else order.stages.all()
    return next((stage for stage in stages if stage.stage_key == "sewing"), None)


def calculate_local_sewing(order, *, stages=None):
    """Calculate one Bangladesh sewing order from stored inputs without writing data."""
    quantity = max(int(getattr(order, "qty_total", 0) or 0), 0)
    completed_quantity = max(int(getattr(order, "completed_quantity", 0) or 0), 0)
    charge_per_piece = getattr(order, "sewing_charge_per_piece_bdt", None)
    cost_per_piece = getattr(order, "sewing_cost_per_piece_bdt", None)
    extra_cost = max(_decimal(getattr(order, "extra_local_cost_bdt", None)), Decimal("0"))

    total_revenue = _money(Decimal(quantity) * _decimal(charge_per_piece))
    cost_available = cost_per_piece is not None and _decimal(cost_per_piece) > 0
    total_cost = None
    profit = None
    margin = None
    if cost_available:
        total_cost = _money(Decimal(quantity) * _decimal(cost_per_piece) + extra_cost)
        profit = _money(total_revenue - total_cost)
        if total_revenue > 0 and total_cost > 0:
            margin = ((profit / total_revenue) * Decimal("100")).quantize(
                PERCENT,
                rounding=ROUND_HALF_UP,
            )

    stage = _sewing_stage(order, stages)
    start_date = getattr(stage, "actual_start", None) if stage else None
    end_date = getattr(stage, "actual_end", None) if stage else None
    days_used = None
    daily_output = None
    if start_date and end_date and end_date >= start_date:
        days_used = (end_date - start_date).days + 1
        if days_used > 0:
            daily_output = (Decimal(completed_quantity) / Decimal(days_used)).quantize(
                PERCENT,
                rounding=ROUND_HALF_UP,
            )

    return {
        "is_local_sewing": is_bangladesh_local_sewing(order),
        "currency": "BDT",
        "quantity": quantity,
        "completed_quantity": completed_quantity,
        "rejected_quantity": int(getattr(order, "qty_reject", 0) or 0),
        "sewing_charge_per_piece": _money(charge_per_piece),
        "total_sewing_revenue": total_revenue,
        "sewing_cost_per_piece": _money(cost_per_piece) if cost_available else None,
        "extra_local_cost": _money(extra_cost),
        "total_sewing_cost": total_cost,
        "cost_available": cost_available,
        "profit": profit,
        "margin": margin,
        "start_date": start_date,
        "end_date": end_date,
        "days_used": days_used,
        "daily_output": daily_output,
    }


def summarize_local_sewing_orders(queryset=None):
    """Return one native-BDT aggregate for dashboard and report consumers."""
    queryset = local_sewing_queryset(queryset)
    money_field = DecimalField(max_digits=24, decimal_places=4)
    revenue_expression = ExpressionWrapper(
        F("qty_total") * F("sewing_charge_per_piece_bdt"),
        output_field=money_field,
    )
    cost_expression = ExpressionWrapper(
        F("qty_total") * F("sewing_cost_per_piece_bdt")
        + Coalesce(F("extra_local_cost_bdt"), Value(Decimal("0"))),
        output_field=money_field,
    )
    completed_filter = Q(status__in=COMPLETED_STATUSES) | Q(
        operational_status__in=COMPLETED_OPERATIONAL_STATUSES
    )
    inactive_filter = (
        completed_filter
        | Q(status__in=CANCELLED_STATUSES)
        | Q(operational_status__in=CANCELLED_OPERATIONAL_STATUSES)
    )
    cost_filter = Q(sewing_cost_per_piece_bdt__gt=0)
    totals = queryset.aggregate(
        order_count=Count("id"),
        in_progress_count=Count("id", filter=~inactive_filter),
        completed_count=Count("id", filter=completed_filter),
        revenue=Coalesce(Sum(revenue_expression), Value(Decimal("0")), output_field=money_field),
        costed_order_count=Count("id", filter=cost_filter),
        costed_revenue=Coalesce(
            Sum(revenue_expression, filter=cost_filter),
            Value(Decimal("0")),
            output_field=money_field,
        ),
        cost=Coalesce(
            Sum(cost_expression, filter=cost_filter),
            Value(Decimal("0")),
            output_field=money_field,
        ),
    )
    revenue = _money(totals["revenue"])
    cost = _money(totals["cost"])
    costed_revenue = _money(totals["costed_revenue"])
    cost_available = bool(totals["costed_order_count"] and cost > 0)
    profit = _money(costed_revenue - cost) if cost_available else None
    margin = None
    if cost_available and costed_revenue > 0:
        margin = ((profit / costed_revenue) * Decimal("100")).quantize(
            PERCENT,
            rounding=ROUND_HALF_UP,
        )
    return {
        "currency": "BDT",
        "order_count": totals["order_count"],
        "in_progress_count": totals["in_progress_count"],
        "completed_count": totals["completed_count"],
        "total_sewing_revenue": revenue,
        "total_sewing_cost": cost if cost_available else None,
        "cost_available": cost_available,
        "costed_order_count": totals["costed_order_count"],
        "profit": profit,
        "margin": margin,
    }


def summarize_canada_export_orders(queryset=None):
    """Keep approved Canada export values in their original currencies."""
    queryset = queryset if queryset is not None else ProductionOrder.objects.all()
    rows = (
        queryset.filter(is_archived=False, order_type__in=CANADA_EXPORT_ORDER_TYPES)
        .exclude(approved_currency__isnull=True)
        .exclude(approved_currency="")
        .values("approved_currency")
        .annotate(amount=Coalesce(Sum("approved_total_value"), Decimal("0")))
    )
    return currency_summary_rows(
        {
            row["approved_currency"].upper(): {"amount": row["amount"]}
            for row in rows
        }
    )


def summarize_production_business_models(queryset=None):
    """Aggregate local sewing and Canada export KPIs in one database query."""
    queryset = queryset if queryset is not None else ProductionOrder.objects.all()
    queryset = queryset.filter(is_archived=False)
    money_field = DecimalField(max_digits=24, decimal_places=4)
    revenue_expression = ExpressionWrapper(
        F("qty_total") * F("sewing_charge_per_piece_bdt"),
        output_field=money_field,
    )
    cost_expression = ExpressionWrapper(
        F("qty_total") * F("sewing_cost_per_piece_bdt")
        + Coalesce(F("extra_local_cost_bdt"), Value(Decimal("0"))),
        output_field=money_field,
    )
    local_filter = Q(order_type=LOCAL_ORDER_TYPE, factory_location=LOCAL_FACTORY)
    completed_filter = local_filter & (
        Q(status__in=COMPLETED_STATUSES)
        | Q(operational_status__in=COMPLETED_OPERATIONAL_STATUSES)
    )
    inactive_filter = completed_filter | (
        local_filter
        & (
            Q(status__in=CANCELLED_STATUSES)
            | Q(operational_status__in=CANCELLED_OPERATIONAL_STATUSES)
        )
    )
    cost_filter = local_filter & Q(sewing_cost_per_piece_bdt__gt=0)
    export_filter = Q(order_type__in=CANADA_EXPORT_ORDER_TYPES)
    totals = queryset.aggregate(
        order_count=Count("id", filter=local_filter),
        in_progress_count=Count("id", filter=local_filter & ~inactive_filter),
        completed_count=Count("id", filter=completed_filter),
        revenue=Coalesce(
            Sum(revenue_expression, filter=local_filter),
            Value(Decimal("0")),
            output_field=money_field,
        ),
        costed_order_count=Count("id", filter=cost_filter),
        costed_revenue=Coalesce(
            Sum(revenue_expression, filter=cost_filter),
            Value(Decimal("0")),
            output_field=money_field,
        ),
        cost=Coalesce(
            Sum(cost_expression, filter=cost_filter),
            Value(Decimal("0")),
            output_field=money_field,
        ),
        export_cad_count=Count("id", filter=export_filter & Q(approved_currency="CAD")),
        export_cad=Coalesce(
            Sum("approved_total_value", filter=export_filter & Q(approved_currency="CAD")),
            Value(Decimal("0")),
            output_field=money_field,
        ),
        export_usd_count=Count("id", filter=export_filter & Q(approved_currency="USD")),
        export_usd=Coalesce(
            Sum("approved_total_value", filter=export_filter & Q(approved_currency="USD")),
            Value(Decimal("0")),
            output_field=money_field,
        ),
        export_bdt_count=Count("id", filter=export_filter & Q(approved_currency="BDT")),
        export_bdt=Coalesce(
            Sum("approved_total_value", filter=export_filter & Q(approved_currency="BDT")),
            Value(Decimal("0")),
            output_field=money_field,
        ),
    )
    revenue = _money(totals["revenue"])
    cost = _money(totals["cost"])
    costed_revenue = _money(totals["costed_revenue"])
    cost_available = bool(totals["costed_order_count"] and cost > 0)
    profit = _money(costed_revenue - cost) if cost_available else None
    margin = None
    if cost_available and costed_revenue > 0:
        margin = ((profit / costed_revenue) * Decimal("100")).quantize(
            PERCENT,
            rounding=ROUND_HALF_UP,
        )
    local = {
        "currency": "BDT",
        "order_count": totals["order_count"],
        "in_progress_count": totals["in_progress_count"],
        "completed_count": totals["completed_count"],
        "total_sewing_revenue": revenue,
        "total_sewing_cost": cost if cost_available else None,
        "cost_available": cost_available,
        "costed_order_count": totals["costed_order_count"],
        "profit": profit,
        "margin": margin,
    }
    export_totals = {
        code: {"amount": _money(totals[f"export_{code.lower()}"])}
        for code in ("CAD", "USD", "BDT")
        if totals[f"export_{code.lower()}_count"]
    }
    return {
        "local_sewing": local,
        "canada_export_revenue_rows": currency_summary_rows(export_totals),
    }
