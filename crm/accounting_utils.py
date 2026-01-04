from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Optional, Tuple, List

from django.db.models import Q, Sum, Count
from django.db.models.functions import Coalesce

from .models import AccountingEntry

ZERO = Decimal("0")
SWING_SUB_TYPE = "Swing"


def parse_year_month(request) -> Tuple[int, Optional[int]]:
    year_raw = (request.GET.get("year") or "").strip()
    month_raw = (request.GET.get("month") or "").strip()

    today = date.today()
    year = today.year
    month: Optional[int] = None

    if year_raw.isdigit():
        year = int(year_raw)

    if month_raw.isdigit():
        m = int(month_raw)
        if 1 <= m <= 12:
            month = m

    return year, month


def build_accounting_qs_for_list(request, year: int, month: Optional[int]):
    side = request.GET.get("side", "ALL")
    main_type = request.GET.get("main_type", "ALL")
    q = (request.GET.get("q") or "").strip()
    has_file = request.GET.get("has_file", "ALL")

    qs = (
        AccountingEntry.objects.filter(date__year=year)
        .select_related("production_order", "shipment", "customer", "opportunity")
        .prefetch_related("attachments")
    )

    if month:
        qs = qs.filter(date__month=month)

    if side in ["CA", "BD"]:
        qs = qs.filter(side=side)

    if main_type and main_type != "ALL":
        qs = qs.filter(main_type=main_type)

    if q:
        qs = qs.filter(
            Q(sub_type__icontains=q)
            | Q(description__icontains=q)
            | Q(transfer_ref__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(opportunity__title__icontains=q)
            | Q(production_order__order_code__icontains=q)
        )

    qs = qs.annotate(att_count=Count("attachments", distinct=True))
    if has_file == "YES":
        qs = qs.filter(att_count__gt=0)
    elif has_file == "NO":
        qs = qs.filter(att_count=0)

    filters = {
        "side": side,
        "main_type": main_type,
        "q": q,
        "has_file": has_file,
    }
    return qs, filters


def build_accounting_qs_for_bd_grid(request, year: int, month: Optional[int]):
    direction = request.GET.get("direction", "ALL")
    main_type = request.GET.get("main_type", "ALL")
    q = (request.GET.get("q") or "").strip()
    order_q = (request.GET.get("order") or "").strip()
    has_file = request.GET.get("has_file", "ALL")

    qs = (
        AccountingEntry.objects.filter(side="BD", date__year=year)
        .select_related("production_order", "shipment", "customer", "opportunity")
        .prefetch_related("attachments")
    )

    if month:
        qs = qs.filter(date__month=month)

    if direction in ["IN", "OUT"]:
        qs = qs.filter(direction=direction)

    if main_type and main_type != "ALL":
        qs = qs.filter(main_type=main_type)

    if order_q:
        qs = qs.filter(production_order__order_code__icontains=order_q)

    if q:
        qs = qs.filter(
            Q(sub_type__icontains=q)
            | Q(description__icontains=q)
            | Q(transfer_ref__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(opportunity__title__icontains=q)
            | Q(production_order__order_code__icontains=q)
        )

    qs = qs.annotate(att_count=Count("attachments", distinct=True))
    if has_file == "YES":
        qs = qs.filter(att_count__gt=0)
    elif has_file == "NO":
        qs = qs.filter(att_count=0)

    filters = {
        "direction": direction,
        "main_type": main_type,
        "q": q,
        "order": order_q,
        "has_file": has_file,
    }
    return qs, filters


def qs_totals(qs):
    total_income_cad = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_cad"), ZERO)
    )["x"]
    total_expense_cad = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_cad"), ZERO)
    )["x"]
    net_cad = total_income_cad - total_expense_cad

    total_in_bdt = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_bdt"), ZERO)
    )["x"]
    total_out_bdt = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_bdt"), ZERO)
    )["x"]
    net_bdt = total_in_bdt - total_out_bdt

    return {
        "total_income_cad": total_income_cad,
        "total_expense_cad": total_expense_cad,
        "net_cad": net_cad,
        "total_in_bdt": total_in_bdt,
        "total_out_bdt": total_out_bdt,
        "net_bdt": net_bdt,
    }


def production_profit_rows(year: int, month: Optional[int]) -> List[Dict]:
    base = Q(date__year=year, production_order__isnull=False)
    if month:
        base &= Q(date__month=month)

    rev_qs = (
        AccountingEntry.objects.filter(base, side="CA", direction="IN")
        .values("production_order_id", "production_order__order_code")
        .annotate(revenue=Coalesce(Sum("amount_cad"), ZERO))
    )

    swing_qs = (
        AccountingEntry.objects.filter(base, side="CA", direction="IN", sub_type=SWING_SUB_TYPE)
        .values("production_order_id")
        .annotate(swing=Coalesce(Sum("amount_cad"), ZERO))
    )

    cost_qs = (
        AccountingEntry.objects.filter(base, side="BD", direction="OUT", main_type__in=["COGS", "EXPENSE"])
        .values("production_order_id")
        .annotate(cost=Coalesce(Sum("amount_cad"), ZERO))
    )

    swing_map = {r["production_order_id"]: r["swing"] for r in swing_qs}
    cost_map = {r["production_order_id"]: r["cost"] for r in cost_qs}

    rows: List[Dict] = []
    for r in rev_qs:
        po_id = r["production_order_id"]
        revenue = r["revenue"] or ZERO
        swing = swing_map.get(po_id, ZERO)
        cost = cost_map.get(po_id, ZERO)

        profit = revenue - cost
        margin_pct = ZERO
        if revenue and revenue != 0:
            margin_pct = (profit / revenue) * Decimal("100")

        rows.append(
            {
                "order_id": po_id,
                "order_code": r.get("production_order__order_code") or "",
                "revenue_cad": revenue,
                "swing_cad": swing,
                "cost_cad": cost,
                "profit_cad": profit,
                "margin_pct": margin_pct,
            }
        )

    rows.sort(key=lambda x: x["profit_cad"])
    return rows


def negative_profit_reasons(year: int, month: Optional[int], top_n: int = 10) -> List[Dict]:
    rows = production_profit_rows(year, month)
    neg = [r for r in rows if (r["profit_cad"] or ZERO) < 0]
    neg = neg[:top_n]

    if not neg:
        return []

    ids = [r["order_id"] for r in neg]

    base = Q(date__year=year, production_order_id__in=ids, side="BD", direction="OUT")
    if month:
        base &= Q(date__month=month)

    reason_qs = (
        AccountingEntry.objects.filter(base)
        .values("production_order_id", "sub_type")
        .annotate(total=Coalesce(Sum("amount_cad"), ZERO))
        .order_by("production_order_id", "-total")
    )

    best_reason: Dict[int, Dict] = {}
    for r in reason_qs:
        po_id = r["production_order_id"]
        if po_id not in best_reason:
            best_reason[po_id] = {
                "sub_type": r.get("sub_type") or "",
                "total_cad": r.get("total") or ZERO,
            }

    out = []
    for r in neg:
        po_id = r["order_id"]
        reason = best_reason.get(po_id, {"sub_type": "", "total_cad": ZERO})
        out.append(
            {
                **r,
                "reason_sub_type": reason["sub_type"],
                "reason_total_cad": reason["total_cad"],
            }
        )
    return out