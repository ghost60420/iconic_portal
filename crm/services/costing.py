from decimal import Decimal, ROUND_HALF_UP

from crm.models import ActualCostEntry, COST_SECTION_CHOICES


INTERNAL_QUANT = Decimal("0.0001")
DISPLAY_QUANT = Decimal("0.01")


def _to_decimal(value):
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _round_internal(value):
    return _to_decimal(value).quantize(INTERNAL_QUANT, rounding=ROUND_HALF_UP)


def _round_display(value):
    return _to_decimal(value).quantize(DISPLAY_QUANT, rounding=ROUND_HALF_UP)


def _section_order():
    return [k for k, _ in COST_SECTION_CHOICES]


def _line_cost_per_piece(line, target_qty, overhead_method, labor_total):
    consumption = _to_decimal(line.consumption_per_piece)
    rate = _to_decimal(line.rate)
    waste = _to_decimal(line.waste_percent)
    setup = _to_decimal(line.setup_cost)

    if line.section == "overhead" and overhead_method == "percent_of_labor":
        percent = rate / Decimal("100")
        base = labor_total * percent
    else:
        base = consumption * rate * (Decimal("1") + (waste / Decimal("100")))

    setup_per_piece = setup / Decimal(target_qty) if target_qty else Decimal("0")
    return _round_internal(base + setup_per_piece)


def calculate_cost_sheet(cost_sheet, target_qty_override=None):
    if target_qty_override is None:
        target_qty = int(cost_sheet.target_quantity or 0)
    else:
        target_qty = int(target_qty_override or 0)
    overhead_method = cost_sheet.overhead_method or "per_piece"

    section_totals = {k: Decimal("0") for k in _section_order()}
    line_rows = []

    # First pass to get labor total
    labor_total = Decimal("0")
    for line in cost_sheet.line_items.all():
        if line.section != "labor":
            continue
        line_total = _line_cost_per_piece(line, target_qty, overhead_method, Decimal("0"))
        labor_total += line_total

    labor_total = _round_internal(labor_total)

    for line in cost_sheet.line_items.all():
        line_total = _line_cost_per_piece(line, target_qty, overhead_method, labor_total)
        section_totals[line.section] = _round_internal(section_totals[line.section] + line_total)

        line_rows.append(
            {
                "id": line.id,
                "section": line.section,
                "item_name": line.item_name,
                "uom": line.uom,
                "consumption_per_piece": _round_internal(line.consumption_per_piece),
                "waste_percent": _round_internal(line.waste_percent),
                "rate": _round_internal(line.rate),
                "setup_cost": _round_internal(line.setup_cost),
                "total_cost_per_piece": line_total,
                "notes": line.notes,
            }
        )

    total_cost_per_piece = _round_internal(sum(section_totals.values()))

    quote_price = _to_decimal(cost_sheet.quote_price_per_piece)
    if quote_price <= 0:
        margin = _to_decimal(cost_sheet.target_margin_percent)
        if margin >= Decimal("100"):
            quote_price = Decimal("0")
        elif margin > 0:
            quote_price = total_cost_per_piece / (Decimal("1") - (margin / Decimal("100")))
        else:
            quote_price = total_cost_per_piece

    quote_price = _round_internal(quote_price)
    profit_per_piece = _round_internal(quote_price - total_cost_per_piece)
    margin_percent = (
        _round_internal((profit_per_piece / quote_price) * Decimal("100"))
        if quote_price
        else Decimal("0")
    )
    total_quote_value = _round_internal(quote_price * Decimal(target_qty))

    chart_data = []
    for section in _section_order():
        total = section_totals.get(section, Decimal("0"))
        percent = Decimal("0")
        if total_cost_per_piece:
            percent = _round_internal((total / total_cost_per_piece) * Decimal("100"))
        chart_data.append(
            {
                "section": section,
                "total": total,
                "percent": percent,
                "display_total": _round_display(total),
                "display_percent": _round_display(percent),
            }
        )

    return {
        "line_rows": line_rows,
        "section_totals": section_totals,
        "total_cost_per_piece": total_cost_per_piece,
        "quote_price_per_piece": quote_price,
        "total_quote_value": total_quote_value,
        "profit_per_piece": profit_per_piece,
        "margin_percent": margin_percent,
        "chart_data": chart_data,
        "display": {
            "total_cost_per_piece": _round_display(total_cost_per_piece),
            "quote_price_per_piece": _round_display(quote_price),
            "total_quote_value": _round_display(total_quote_value),
            "profit_per_piece": _round_display(profit_per_piece),
            "margin_percent": _round_display(margin_percent),
            "section_totals": {
                k: _round_display(v) for k, v in section_totals.items()
            },
        },
    }


def calculate_actuals(cost_sheet, production_order):
    produced_qty = int(getattr(production_order, "qty_total", 0) or 0)
    entries = ActualCostEntry.objects.filter(production_order=production_order)
    if cost_sheet:
        entries = entries.filter(cost_sheet=cost_sheet)

    section_totals = {k: Decimal("0") for k in _section_order()}
    for entry in entries:
        section_totals[entry.section] = _round_internal(
            section_totals[entry.section] + _to_decimal(entry.actual_total_cost)
        )

    actual_total_cost = _round_internal(sum(section_totals.values()))
    actual_cost_per_piece = (
        _round_internal(actual_total_cost / Decimal(produced_qty)) if produced_qty else Decimal("0")
    )

    return {
        "produced_qty": produced_qty,
        "section_totals": section_totals,
        "actual_total_cost": actual_total_cost,
        "actual_cost_per_piece": actual_cost_per_piece,
        "display": {
            "actual_total_cost": _round_display(actual_total_cost),
            "actual_cost_per_piece": _round_display(actual_cost_per_piece),
            "section_totals": {
                k: _round_display(v) for k, v in section_totals.items()
            },
        },
    }


def build_variance_report(cost_sheet, production_order):
    if not cost_sheet or not production_order:
        return None

    standard = calculate_cost_sheet(cost_sheet)
    actual = calculate_actuals(cost_sheet, production_order)

    produced_qty = actual["produced_qty"]
    quoted_price = standard["quote_price_per_piece"]
    standard_cost = standard["total_cost_per_piece"]
    actual_cost = actual["actual_cost_per_piece"]

    variance_per_piece = _round_internal(actual_cost - standard_cost)
    total_variance = _round_internal(variance_per_piece * Decimal(produced_qty))

    margin_before = (
        _round_internal(((quoted_price - standard_cost) / quoted_price) * Decimal("100"))
        if quoted_price
        else Decimal("0")
    )
    margin_after = (
        _round_internal(((quoted_price - actual_cost) / quoted_price) * Decimal("100"))
        if quoted_price
        else Decimal("0")
    )

    label_map = dict(COST_SECTION_CHOICES)
    variance_rows = []
    for section in _section_order():
        std_per_piece = standard["section_totals"].get(section, Decimal("0"))
        std_total = _round_internal(std_per_piece * Decimal(produced_qty))
        act_total = actual["section_totals"].get(section, Decimal("0"))
        variance_rows.append(
            {
                "section": section,
                "section_label": label_map.get(section, section),
                "standard_total": std_total,
                "actual_total": act_total,
                "difference": _round_internal(act_total - std_total),
                "display_standard_total": _round_display(std_total),
                "display_actual_total": _round_display(act_total),
                "display_difference": _round_display(act_total - std_total),
            }
        )

    return {
        "quoted_price_per_piece": quoted_price,
        "standard_cost_per_piece": standard_cost,
        "actual_cost_per_piece": actual_cost,
        "variance_per_piece": variance_per_piece,
        "total_variance": total_variance,
        "margin_before": margin_before,
        "margin_after": margin_after,
        "variance_rows": variance_rows,
        "display": {
            "quoted_price_per_piece": _round_display(quoted_price),
            "standard_cost_per_piece": _round_display(standard_cost),
            "actual_cost_per_piece": _round_display(actual_cost),
            "variance_per_piece": _round_display(variance_per_piece),
            "total_variance": _round_display(total_variance),
            "margin_before": _round_display(margin_before),
            "margin_after": _round_display(margin_after),
        },
    }
