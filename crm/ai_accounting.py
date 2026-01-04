from decimal import Decimal
from typing import Dict, Optional, Tuple
from datetime import date

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.db.models import DecimalField

from .models import AccountingEntry

ZERO = Decimal("0")


def _month_label(year: int, month: int) -> str:
    try:
        d = date(year, month, 1)
        return d.strftime("%B %Y")
    except Exception:
        return f"{year}-{month:02d}"


def _prev_year_month(year: int, month: int) -> Tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _safe_decimal(v) -> Decimal:
    try:
        return Decimal(v)
    except Exception:
        return ZERO


def get_ai_insights_for_view(
    *,
    year: int,
    month: Optional[int],
    currency_mode: str,
    side: Optional[str] = None,
    main_type: Optional[str] = None,
) -> Dict:
    if not month:
        return {
            "month_label": f"Year {year}",
            "cost_top": [],
            "spikes": [],
            "neg_orders": [],
        }

    month_label = _month_label(year, month)
    amount_field = "amount_cad" if currency_mode == "CAD" else "amount_bdt"

    base_filters = {
        "date__year": year,
        "date__month": month,
        "direction": "OUT",
    }

    now_cost_qs = AccountingEntry.objects.filter(**base_filters)

    if side in ["CA", "BD"]:
        now_cost_qs = now_cost_qs.filter(side=side)

    if main_type and main_type != "ALL":
        now_cost_qs = now_cost_qs.filter(main_type=main_type)

    cost_top = (
        now_cost_qs.values("main_type", "sub_type")
        .annotate(
            total=Coalesce(
                Sum(amount_field),
                ZERO,
                output_field=DecimalField(),
            )
        )
        .order_by("-total")[:5]
    )

    cost_top_list = []
    for row in cost_top:
        cost_top_list.append(
            {
                "main_type": row.get("main_type") or "",
                "sub_type": row.get("sub_type") or "",
                "total": _safe_decimal(row.get("total")),
            }
        )

    py, pm = _prev_year_month(year, month)

    prev_cost_qs = AccountingEntry.objects.filter(
        date__year=py,
        date__month=pm,
        direction="OUT",
    )

    if side in ["CA", "BD"]:
        prev_cost_qs = prev_cost_qs.filter(side=side)

    if main_type and main_type != "ALL":
        prev_cost_qs = prev_cost_qs.filter(main_type=main_type)

    prev_map = {
        (r["main_type"] or "", r["sub_type"] or ""): _safe_decimal(r["total"])
        for r in prev_cost_qs.values("main_type", "sub_type").annotate(
            total=Coalesce(
                Sum(amount_field),
                ZERO,
                output_field=DecimalField(),
            )
        )
    }

    now_map = {
        (r["main_type"] or "", r["sub_type"] or ""): _safe_decimal(r["total"])
        for r in now_cost_qs.values("main_type", "sub_type").annotate(
            total=Coalesce(
                Sum(amount_field),
                ZERO,
                output_field=DecimalField(),
            )
        )
    }

    spikes = []
    for key, now_val in now_map.items():
        prev_val = prev_map.get(key, ZERO)
        inc = now_val - prev_val

        if prev_val > ZERO:
            pct = inc / prev_val
        else:
            pct = Decimal("999") if now_val > ZERO else ZERO

        min_inc = Decimal("5000") if currency_mode == "BDT" else Decimal("500")

        if inc > min_inc and pct >= Decimal("0.20"):
            spikes.append(
                {
                    "main_type": key[0],
                    "sub_type": key[1],
                    "prev": prev_val,
                    "now": now_val,
                    "increase": inc,
                }
            )

    spikes.sort(key=lambda x: x["increase"], reverse=True)
    spikes = spikes[:5]

    return {
        "month_label": month_label,
        "cost_top": cost_top_list,
        "spikes": spikes,
        "neg_orders": [],
    }
def suggest_accounting_fields_from_text(text: str) -> dict[str, str]:
    t = (text or "").strip().lower()

    side = ""
    direction = ""
    main_type = ""
    sub_type = ""

    if "bangladesh" in t or "bd " in t or " bdt" in t:
        side = "BD"
    if "canada" in t or "ca " in t or " cad" in t:
        side = "CA"

    in_words = [
        "received", "receive", "payment", "paid by", "income", "deposit", "invoice paid"
    ]
    out_words = [
        "paid", "pay", "expense", "cost", "purchase", "bought", "rent", "salary",
        "wage", "bill", "utility"
    ]

    if any(w in t for w in in_words):
        direction = "IN"
    if any(w in t for w in out_words):
        direction = "OUT"

    if any(w in t for w in ["transfer", "send to", "sent to", "wire", "bank transfer", "remit"]):
        main_type = "TRANSFER"
        sub_type = "Transfer"

    elif any(w in t for w in ["tax", "vat", "gst", "hst", "duty"]):
        main_type = "TAX"
        sub_type = "Tax"

    elif any(w in t for w in [
        "fabric", "materials", "raw", "yarn", "knit", "dye", "print", "embroidery",
        "trim", "trims", "accessories", "label", "packing", "poly", "carton"
    ]):
        main_type = "COGS"
        sub_type = "Materials"

    elif any(w in t for w in ["courier", "shipping", "fedex", "dhl", "ups", "delivery", "freight"]):
        main_type = "EXPENSE"
        sub_type = "Shipping"

    elif any(w in t for w in ["salary", "wage", "payroll", "overtime", "bonus"]):
        main_type = "EXPENSE"
        sub_type = "Payroll"

    elif any(w in t for w in ["rent", "lease"]):
        main_type = "EXPENSE"
        sub_type = "Rent"

    elif any(w in t for w in ["electric", "water", "gas", "internet", "utility"]):
        main_type = "EXPENSE"
        sub_type = "Utilities"

    else:
        if direction == "IN":
            main_type = "INCOME"
            sub_type = "Client payment"
        elif direction == "OUT":
            main_type = "EXPENSE"
            sub_type = "General"

    return {
        "side": side,
        "direction": direction,
        "main_type": main_type,
        "sub_type": sub_type,
    }