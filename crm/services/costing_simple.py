from decimal import Decimal, ROUND_HALF_UP

from .costing_currency import cad_from_bdt


INTERNAL_QUANT = Decimal("0.0001")
DISPLAY_QUANT = Decimal("0.01")


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


def calculate_cost_sheet_simple(cost_sheet):
    qty = int(cost_sheet.quantity or 0)

    fabric_cost = _to_decimal(cost_sheet.fabric_cost_per_piece)
    wastage = _to_decimal(cost_sheet.fabric_wastage_percent)
    fabric_effective = _round_internal(fabric_cost * (Decimal("1") + (wastage / Decimal("100"))))

    rib_cost = _to_decimal(cost_sheet.rib_cost_per_piece)
    woven_fabric_cost = _to_decimal(cost_sheet.woven_fabric_cost_per_piece)
    zipper_cost = _to_decimal(cost_sheet.zipper_cost_per_piece)
    zipper_puller_cost = _to_decimal(cost_sheet.zipper_puller_cost_per_piece)
    button_cost = _to_decimal(cost_sheet.button_cost_per_piece)
    thread_cost = _to_decimal(cost_sheet.thread_cost_per_piece)
    lining_cost = _to_decimal(cost_sheet.lining_cost_per_piece)
    velcro_cost = _to_decimal(cost_sheet.velcro_cost_per_piece)
    neck_tape_cost = _to_decimal(cost_sheet.neck_tape_cost_per_piece)
    elastic_cost = _to_decimal(cost_sheet.elastic_cost_per_piece)
    collar_cuff_cost = _to_decimal(cost_sheet.collar_cuff_cost_per_piece)
    ring_cost = _to_decimal(cost_sheet.ring_cost_per_piece)
    buckle_clip_cost = _to_decimal(cost_sheet.buckle_clip_cost_per_piece)
    main_label_cost = _to_decimal(cost_sheet.main_label_cost_per_piece)
    care_label_cost = _to_decimal(cost_sheet.care_label_cost_per_piece)
    hang_tag_cost = _to_decimal(cost_sheet.hang_tag_cost_per_piece)
    conveyance_cost = _to_decimal(cost_sheet.conveyance_cost_per_piece)

    trim_cost = _to_decimal(cost_sheet.trim_cost_per_piece)
    labor_cost = _to_decimal(cost_sheet.labor_cost_per_piece)
    overhead_cost = _to_decimal(cost_sheet.overhead_cost_per_piece)
    process_cost = _to_decimal(cost_sheet.process_cost_per_piece)
    packaging_cost = _to_decimal(cost_sheet.packaging_cost_per_piece)
    freight_cost = _to_decimal(cost_sheet.freight_cost_per_piece)
    testing_cost = _to_decimal(cost_sheet.testing_cost_per_piece)
    other_cost = _to_decimal(cost_sheet.other_cost_per_piece)

    total_cost_per_piece = _round_internal(
        fabric_effective
        + rib_cost
        + woven_fabric_cost
        + zipper_cost
        + zipper_puller_cost
        + button_cost
        + thread_cost
        + lining_cost
        + velcro_cost
        + neck_tape_cost
        + elastic_cost
        + collar_cuff_cost
        + ring_cost
        + buckle_clip_cost
        + main_label_cost
        + care_label_cost
        + hang_tag_cost
        + conveyance_cost
        + trim_cost
        + labor_cost
        + overhead_cost
        + process_cost
        + packaging_cost
        + freight_cost
        + testing_cost
        + other_cost
    )

    total_order_cost = _round_internal(total_cost_per_piece * Decimal(qty))

    quote_price = _to_decimal(cost_sheet.quote_price_per_piece)
    profit_per_piece = _round_internal(quote_price - total_cost_per_piece)
    total_profit = _round_internal(profit_per_piece * Decimal(qty))
    margin_percent = (
        _round_internal((profit_per_piece / quote_price) * Decimal("100"))
        if quote_price
        else Decimal("0")
    )

    exchange_rate = cost_sheet.exchange_rate_bdt_per_cad
    cad_available = bool(exchange_rate and _to_decimal(exchange_rate) > 0)

    cad_values = {
        "total_cost_per_piece": cad_from_bdt(total_cost_per_piece, exchange_rate),
        "quote_price_per_piece": cad_from_bdt(quote_price, exchange_rate),
        "profit_per_piece": cad_from_bdt(profit_per_piece, exchange_rate),
        "total_order_cost": cad_from_bdt(total_order_cost, exchange_rate),
        "total_profit": cad_from_bdt(total_profit, exchange_rate),
    }

    return {
        "fabric_effective_cost_per_piece": fabric_effective,
        "total_cost_per_piece": total_cost_per_piece,
        "total_order_cost": total_order_cost,
        "profit_per_piece": profit_per_piece,
        "total_profit": total_profit,
        "margin_percent": margin_percent,
        "exchange_rate": exchange_rate,
        "cad_available": cad_available,
        "cad": cad_values,
        "display": {
            "fabric_effective_cost_per_piece": _round_display(fabric_effective),
            "total_cost_per_piece": _round_display(total_cost_per_piece),
            "total_order_cost": _round_display(total_order_cost),
            "profit_per_piece": _round_display(profit_per_piece),
            "total_profit": _round_display(total_profit),
            "margin_percent": _round_display(margin_percent),
            "quote_price_per_piece": _round_display(quote_price),
        },
    }
