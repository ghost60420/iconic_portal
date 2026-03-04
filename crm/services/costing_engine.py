from decimal import Decimal, ROUND_HALF_UP

from crm.models import (
    CostingHeader,
    CostingLineItem,
    NEW_COSTING_CATEGORY_CHOICES,
)


INTERNAL_QUANT = Decimal("0.0001")
DISPLAY_QUANT = Decimal("0.01")
CATEGORY_KEYS = [key for key, _ in NEW_COSTING_CATEGORY_CHOICES]


def _to_decimal(value):
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except Exception:
        return Decimal("0")


def _round_internal(value):
    return _to_decimal(value).quantize(INTERNAL_QUANT, rounding=ROUND_HALF_UP)


def _round_display(value):
    return _to_decimal(value).quantize(DISPLAY_QUANT, rounding=ROUND_HALF_UP)


def _pct(value):
    return _to_decimal(value) / Decimal("100")


def _line_cost_per_piece(line, order_qty):
    cif = _to_decimal(line.unit_price) + _to_decimal(line.freight)
    consumption = _to_decimal(line.consumption_value)
    wastage = _pct(line.wastage_percent)

    base = Decimal("0")
    if line.denominator_value and _to_decimal(line.denominator_value) > 0:
        base = (cif / _to_decimal(line.denominator_value)) * consumption
    else:
        base = cif * consumption

    base = base * (Decimal("1") + wastage)

    if line.uom == "order":
        if order_qty > 0:
            base = base / Decimal(order_qty)
        else:
            base = Decimal("0")

    return _round_internal(base)


def _labor_from_smv(smv):
    if not smv:
        return Decimal("0")
    smv_total = _to_decimal(smv.machine_smv) + _to_decimal(smv.finishing_smv)
    cpm = _to_decimal(smv.cpm)
    if smv_total <= 0 or cpm <= 0:
        return Decimal("0")
    eff = _to_decimal(smv.efficiency_costing)
    if eff <= 0:
        eff = Decimal("100")
    return _round_internal((smv_total * cpm) / (eff / Decimal("100")))


def compute_costing(costing_id):
    costing = CostingHeader.objects.select_related("opportunity", "customer").prefetch_related("line_items").filter(pk=costing_id).first()
    if not costing:
        return None

    order_qty = int(costing.order_quantity or 0)

    category_totals = {key: Decimal("0") for key in CATEGORY_KEYS}
    line_rows = []
    for line in costing.line_items.all():
        line_total = _line_cost_per_piece(line, order_qty)
        category_totals[line.category] = _round_internal(category_totals.get(line.category, Decimal("0")) + line_total)
        line_rows.append(
            {
                "id": line.id,
                "category": line.category,
                "item_name": line.item_name,
                "item_reference": line.item_reference,
                "supplier": line.supplier,
                "uom": line.uom,
                "unit_price": _round_internal(line.unit_price),
                "freight": _round_internal(line.freight),
                "consumption_value": _round_internal(line.consumption_value),
                "wastage_percent": _round_internal(line.wastage_percent),
                "denominator_value": _round_internal(line.denominator_value) if line.denominator_value else None,
                "placement": line.placement,
                "color": line.color,
                "gsm": line.gsm,
                "cut_width": line.cut_width,
                "remarks": line.remarks,
                "cost_per_piece": line_total,
            }
        )

    fabric_base = category_totals.get("fabric", Decimal("0"))
    sewing_trim_base = category_totals.get("sewing_trim", Decimal("0"))
    packaging_trim_base = category_totals.get("packaging_trim", Decimal("0"))
    other_base = category_totals.get("other", Decimal("0"))

    trims_base = _round_internal(sewing_trim_base + packaging_trim_base)

    smv = getattr(costing, "smv", None)
    labor_cost_per_piece = _labor_from_smv(smv)

    fabric_finance = _round_internal(fabric_base * _pct(costing.finance_percent_fabric))
    trims_finance = _round_internal(trims_base * _pct(costing.finance_percent_trims))

    total_cost_per_piece = _round_internal(
        fabric_base
        + trims_base
        + other_base
        + labor_cost_per_piece
        + fabric_finance
        + trims_finance
    )

    fob_per_piece = _to_decimal(costing.manual_fob_per_piece)
    if fob_per_piece <= 0:
        target_margin = _to_decimal(costing.target_margin_percent)
        if target_margin > 0:
            margin_ratio = target_margin / Decimal("100")
            if margin_ratio < Decimal("1"):
                fob_per_piece = _round_internal(total_cost_per_piece / (Decimal("1") - margin_ratio))
            else:
                fob_per_piece = Decimal("0")
        else:
            fob_per_piece = Decimal("0")
    else:
        fob_per_piece = _round_internal(fob_per_piece)

    profit_per_piece = _round_internal(fob_per_piece - total_cost_per_piece) if fob_per_piece else Decimal("0")
    margin_percent = (
        _round_internal((profit_per_piece / fob_per_piece) * Decimal("100"))
        if fob_per_piece
        else Decimal("0")
    )

    commission_percent = _to_decimal(costing.commission_percent)
    final_offer_fob_per_piece = _round_internal(fob_per_piece * (Decimal("1") + (commission_percent / Decimal("100")))) if fob_per_piece else Decimal("0")

    total_cost_order = _round_internal(total_cost_per_piece * Decimal(order_qty))
    total_sales_order = _round_internal(fob_per_piece * Decimal(order_qty))
    total_profit_order = _round_internal(profit_per_piece * Decimal(order_qty))
    total_final_offer_order = _round_internal(final_offer_fob_per_piece * Decimal(order_qty))

    breakdown = {
        "fabric": fabric_base,
        "sewing_trim": sewing_trim_base,
        "packaging_trim": packaging_trim_base,
        "trims": trims_base,
        "other": other_base,
        "labor": labor_cost_per_piece,
        "fabric_finance": fabric_finance,
        "trims_finance": trims_finance,
    }

    breakdown_order = {k: _round_internal(v * Decimal(order_qty)) for k, v in breakdown.items()}
    display_breakdown = {k: _round_display(v) for k, v in breakdown.items()}
    display_breakdown_order = {k: _round_display(v) for k, v in breakdown_order.items()}

    return {
        "costing": costing,
        "order_quantity": order_qty,
        "line_rows": line_rows,
        "category_totals": category_totals,
        "breakdown": breakdown,
        "breakdown_order": breakdown_order,
        "fabric_base": fabric_base,
        "sewing_trim_base": sewing_trim_base,
        "packaging_trim_base": packaging_trim_base,
        "trims_base": trims_base,
        "other_base": other_base,
        "labor_cost_per_piece": labor_cost_per_piece,
        "fabric_finance": fabric_finance,
        "trims_finance": trims_finance,
        "total_cost_per_piece": total_cost_per_piece,
        "fob_per_piece": fob_per_piece,
        "profit_per_piece": profit_per_piece,
        "margin_percent": margin_percent,
        "final_offer_fob_per_piece": final_offer_fob_per_piece,
        "total_cost_order": total_cost_order,
        "total_sales_order": total_sales_order,
        "total_profit_order": total_profit_order,
        "total_final_offer_order": total_final_offer_order,
        "display": {
            "fabric_base": _round_display(fabric_base),
            "sewing_trim_base": _round_display(sewing_trim_base),
            "packaging_trim_base": _round_display(packaging_trim_base),
            "trims_base": _round_display(trims_base),
            "other_base": _round_display(other_base),
            "labor_cost_per_piece": _round_display(labor_cost_per_piece),
            "fabric_finance": _round_display(fabric_finance),
            "trims_finance": _round_display(trims_finance),
            "total_cost_per_piece": _round_display(total_cost_per_piece),
            "fob_per_piece": _round_display(fob_per_piece),
            "profit_per_piece": _round_display(profit_per_piece),
            "margin_percent": _round_display(margin_percent),
            "final_offer_fob_per_piece": _round_display(final_offer_fob_per_piece),
            "total_cost_order": _round_display(total_cost_order),
            "total_sales_order": _round_display(total_sales_order),
            "total_profit_order": _round_display(total_profit_order),
            "total_final_offer_order": _round_display(total_final_offer_order),
            "breakdown": display_breakdown,
            "breakdown_order": display_breakdown_order,
        },
    }


def validate_costing(costing, calc):
    errors = []
    warnings = []

    if (costing.order_quantity or 0) <= 0:
        errors.append("Order quantity must be greater than 0.")

    if costing.exchange_rate is not None and _to_decimal(costing.exchange_rate) <= 0:
        errors.append("Exchange rate must be greater than 0.")

    for line in costing.line_items.all():
        if _to_decimal(line.unit_price) < 0 or _to_decimal(line.freight) < 0 or _to_decimal(line.consumption_value) < 0:
            errors.append(f"Negative values are not allowed for {line.item_name}.")
            break
        if _to_decimal(line.wastage_percent) < 0 or _to_decimal(line.wastage_percent) > Decimal("50"):
            errors.append(f"Wastage percent must be between 0 and 50 for {line.item_name}.")
            break
        if line.denominator_value is not None and _to_decimal(line.denominator_value) <= 0:
            errors.append(f"Denominator must be greater than 0 for {line.item_name}.")
            break

    if calc["fob_per_piece"] <= 0:
        errors.append("FOB per piece is required. Set manual FOB or target margin.")

    fabric_base = calc["fabric_base"]
    trims_base = calc["trims_base"]
    if trims_base and fabric_base and trims_base > (fabric_base * Decimal("1.5")):
        warnings.append("Trims cost is unusually high compared to fabric.")
    if fabric_base and trims_base and fabric_base < (trims_base * Decimal("0.5")):
        warnings.append("Fabric cost is unusually low compared to trims.")

    if calc["margin_percent"] < Decimal("5"):
        warnings.append("Margin is below the minimum target.")

    return errors, warnings
