from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q, Sum
from django.utils import timezone

from crm.models import ActualCostEntry, Invoice, OrderLifecycle, Shipment
from crm.services.costing_engine import compute_costing


MONEY = Decimal("0.01")


def _d(value):
    if value in ("", None):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value):
    return _d(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _user_or_none(user):
    return user if user and getattr(user, "is_authenticated", False) else None


def can_view_lifecycle_profit(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    access = getattr(user, "access", None)
    if not access:
        return False
    return any(
        bool(getattr(access, flag, False))
        for flag in ("can_accounting_ca", "can_accounting_bd", "can_costing", "can_costing_approve")
    )


def _first_existing(**links):
    query = Q()
    invoice = links.get("invoice")
    production_order = links.get("production_order")
    shipping_record = links.get("shipping_record")
    costing = links.get("costing")
    quotation = links.get("quotation")
    opportunity = links.get("opportunity")

    if invoice:
        query |= Q(invoice=invoice)
    if production_order:
        query |= Q(production_order=production_order)
    if shipping_record:
        query |= Q(shipping_record=shipping_record)
    if costing:
        query |= Q(costing=costing) | Q(quotation=costing)
    if quotation:
        query |= Q(quotation=quotation) | Q(costing=quotation)
    if opportunity:
        query |= Q(opportunity=opportunity)

    if not query:
        return None
    return OrderLifecycle.objects.filter(query).order_by("-updated_at", "-id").first()


def _infer_status(lifecycle):
    if lifecycle.status == "cancelled":
        return "cancelled"
    shipment = lifecycle.shipping_record
    if shipment and shipment.status == "delivered":
        return "completed"
    if shipment:
        return "shipping"
    if lifecycle.production_order_id:
        return "production"
    if lifecycle.invoice_id:
        return "invoice"
    if lifecycle.quotation_id:
        return "quotation"
    if lifecycle.costing_id:
        return "costing"
    return "lead"


def _set_if_empty(obj, field_name, value):
    if value is not None and not getattr(obj, f"{field_name}_id", None):
        setattr(obj, field_name, value)


def _text_matches(row, needles):
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("item_name", "category", "remarks", "description", "item_reference")
    ).lower()
    return any(needle in haystack for needle in needles)


def _costing_buckets(costing):
    zero = Decimal("0")
    buckets = {
        "sewing_cost": zero,
        "fabric_cost": zero,
        "print_cost": zero,
        "trim_cost": zero,
        "sampling_cost": zero,
        "costing_total": zero,
        "quote_total": zero,
    }
    if not costing:
        return buckets

    calc = compute_costing(costing.id)
    if not calc:
        return buckets

    qty = Decimal(int(calc.get("order_quantity") or 0))
    breakdown_order = calc.get("breakdown_order") or {}
    buckets["sewing_cost"] = _d(breakdown_order.get("labor"))
    buckets["fabric_cost"] = _d(breakdown_order.get("fabric"))
    buckets["trim_cost"] = _d(breakdown_order.get("trims"))
    buckets["costing_total"] = _d(calc.get("total_cost_order"))
    buckets["quote_total"] = _d(calc.get("total_final_offer_order")) or _d(calc.get("total_sales_order"))

    print_total = Decimal("0")
    sampling_total = Decimal("0")
    for row in calc.get("line_rows") or []:
        line_total = _d(row.get("cost_per_piece")) * qty
        if row.get("category") == "wash_process" or _text_matches(row, ("print", "screen", "sublimation", "embroidery")):
            print_total += line_total
        if _text_matches(row, ("sample", "sampling", "development")):
            sampling_total += line_total
    buckets["print_cost"] = print_total
    buckets["sampling_cost"] = sampling_total
    return buckets


def _shipment_cost_for_currency(shipment, currency):
    if not shipment:
        return Decimal("0")
    currency = (currency or "").upper().strip()
    if currency == "BDT":
        return _d(shipment.cost_bdt)
    if currency == "CAD":
        return _d(shipment.cost_cad)
    return Decimal("0")


def build_lifecycle_profit_breakdown(lifecycle):
    invoice = lifecycle.invoice
    costing = lifecycle.quotation or lifecycle.costing
    production_order = lifecycle.production_order
    shipment = lifecycle.shipping_record

    if not shipment and production_order:
        shipment = production_order.shipments.order_by("-ship_date", "-created_at", "-id").first()

    currency = ""
    if invoice:
        currency = invoice.currency or ""
    elif costing:
        currency = costing.currency or ""

    costing_buckets = _costing_buckets(costing)
    invoice_total = _d(getattr(invoice, "total_amount", Decimal("0"))) if invoice else Decimal("0")
    quote_total = costing_buckets["quote_total"]
    revenue = invoice_total or quote_total

    sewing_cost = _d(getattr(invoice, "sewing_charge", Decimal("0"))) if invoice else Decimal("0")
    if sewing_cost <= 0:
        sewing_cost = costing_buckets["sewing_cost"]

    fabric_cost = costing_buckets["fabric_cost"]
    print_cost = costing_buckets["print_cost"]
    trim_cost = costing_buckets["trim_cost"]
    sampling_cost = costing_buckets["sampling_cost"]
    shipping_cost = _shipment_cost_for_currency(shipment, currency)

    expected_internal = sewing_cost
    if invoice:
        expected_internal += _d(invoice.other_internal_cost)
    elif costing_buckets["costing_total"] > 0:
        expected_internal = costing_buckets["costing_total"]

    known_formula_cost = sewing_cost + fabric_cost + print_cost + trim_cost + sampling_cost
    other_internal_cost = expected_internal - known_formula_cost
    if other_internal_cost < 0:
        other_internal_cost = Decimal("0")

    actual_production_cost = Decimal("0")
    if production_order:
        actual_production_cost = _d(
            ActualCostEntry.objects.filter(production_order=production_order)
            .aggregate(total=Sum("actual_total_cost"))
            .get("total")
        )

    can_use_actual_production = bool(actual_production_cost > 0 and (currency or "").upper() == "BDT")
    if can_use_actual_production:
        total_cost = actual_production_cost + shipping_cost
    else:
        total_cost = (
            sewing_cost
            + fabric_cost
            + print_cost
            + shipping_cost
            + trim_cost
            + sampling_cost
            + other_internal_cost
        )

    net_profit = revenue - total_cost
    margin = (net_profit / revenue) * Decimal("100") if revenue > 0 else Decimal("0")

    return {
        "currency": currency,
        "invoice_total": revenue,
        "sewing_cost": sewing_cost,
        "fabric_cost": fabric_cost,
        "print_cost": print_cost,
        "shipping_cost": shipping_cost,
        "trim_cost": trim_cost,
        "sampling_cost": sampling_cost,
        "other_internal_cost": other_internal_cost,
        "actual_production_cost": actual_production_cost,
        "can_use_actual_production": can_use_actual_production,
        "total_cost": total_cost,
        "net_profit": net_profit,
        "margin": margin,
        "display": {
            "invoice_total": _money(revenue),
            "sewing_cost": _money(sewing_cost),
            "fabric_cost": _money(fabric_cost),
            "print_cost": _money(print_cost),
            "shipping_cost": _money(shipping_cost),
            "trim_cost": _money(trim_cost),
            "sampling_cost": _money(sampling_cost),
            "other_internal_cost": _money(other_internal_cost),
            "actual_production_cost": _money(actual_production_cost),
            "total_cost": _money(total_cost),
            "net_profit": _money(net_profit),
            "margin": _money(margin),
        },
    }


def refresh_lifecycle_financials(lifecycle):
    breakdown = build_lifecycle_profit_breakdown(lifecycle)
    lifecycle.estimated_revenue = breakdown["display"]["invoice_total"]
    lifecycle.estimated_cost = breakdown["display"]["total_cost"]
    lifecycle.estimated_profit = breakdown["display"]["net_profit"]
    lifecycle.estimated_margin = breakdown["display"]["margin"]
    return lifecycle


def _save_lifecycle(lifecycle):
    lifecycle.status = _infer_status(lifecycle)
    refresh_lifecycle_financials(lifecycle)
    lifecycle.save()
    return lifecycle


def refresh_lifecycle(lifecycle):
    return _save_lifecycle(lifecycle)


def _upsert_lifecycle(user=None, **links):
    lifecycle = _first_existing(**links)
    if lifecycle is None:
        lifecycle = OrderLifecycle(created_by=_user_or_none(user))

    customer = links.get("customer")
    lead = links.get("lead")
    opportunity = links.get("opportunity")
    costing = links.get("costing")
    quotation = links.get("quotation")
    invoice = links.get("invoice")
    production_order = links.get("production_order")
    shipping_record = links.get("shipping_record")

    if shipping_record:
        lifecycle.shipping_record = shipping_record
        production_order = production_order or getattr(shipping_record, "order", None)
        opportunity = opportunity or getattr(shipping_record, "opportunity", None)
        customer = customer or getattr(shipping_record, "customer", None)
    if production_order:
        lifecycle.production_order = production_order
        opportunity = opportunity or getattr(production_order, "opportunity", None)
        customer = customer or getattr(production_order, "customer", None)
        lead = lead or getattr(production_order, "lead", None)
        costing = costing or getattr(production_order, "costing_header", None)
    if invoice:
        lifecycle.invoice = invoice
        production_order = production_order or getattr(invoice, "order", None)
        customer = customer or getattr(invoice, "customer", None)
        costing = costing or getattr(invoice, "costing_header", None)
    if quotation:
        lifecycle.quotation = quotation
        costing = costing or quotation
        opportunity = opportunity or getattr(quotation, "opportunity", None)
        customer = customer or getattr(quotation, "customer", None)
    if costing:
        lifecycle.costing = costing
        opportunity = opportunity or getattr(costing, "opportunity", None)
        customer = customer or getattr(costing, "customer", None)
        if getattr(costing, "quotation_number", ""):
            lifecycle.quotation = costing
    if opportunity:
        lifecycle.opportunity = opportunity
        lead = lead or getattr(opportunity, "lead", None)
        customer = customer or getattr(opportunity, "customer", None)
    if lead:
        lifecycle.lead = lead
        customer = customer or getattr(lead, "customer", None)
    if customer:
        lifecycle.customer = customer

    if production_order and not lifecycle.production_order_id:
        lifecycle.production_order = production_order
    return _save_lifecycle(lifecycle)


def create_lifecycle_from_costing(costing, user=None):
    return _upsert_lifecycle(user=user, costing=costing)


def create_lifecycle_from_quotation(quotation, user=None):
    return _upsert_lifecycle(user=user, costing=quotation, quotation=quotation)


def create_lifecycle_from_invoice(invoice, user=None):
    return _upsert_lifecycle(
        user=user,
        invoice=invoice,
        costing=getattr(invoice, "costing_header", None),
        production_order=getattr(invoice, "order", None),
        customer=getattr(invoice, "customer", None),
    )


def create_lifecycle_from_production(production_order, user=None):
    return _upsert_lifecycle(
        user=user,
        production_order=production_order,
        costing=getattr(production_order, "costing_header", None),
        opportunity=getattr(production_order, "opportunity", None),
        lead=getattr(production_order, "lead", None),
        customer=getattr(production_order, "customer", None),
    )


def create_lifecycle_from_shipping(shipping_record, user=None):
    order = getattr(shipping_record, "order", None)
    return _upsert_lifecycle(
        user=user,
        shipping_record=shipping_record,
        production_order=order,
        opportunity=getattr(shipping_record, "opportunity", None) or getattr(order, "opportunity", None),
        customer=getattr(shipping_record, "customer", None) or getattr(order, "customer", None),
    )


def lifecycle_timeline_steps(lifecycle):
    return [
        {
            "key": "lead",
            "label": "Lead",
            "date": getattr(lifecycle.lead, "created_date", None),
            "record": lifecycle.lead,
            "url_name": "lead_detail",
            "is_done": bool(lifecycle.lead_id),
            "amount": None,
            "notes": getattr(lifecycle.lead, "lead_status", "") if lifecycle.lead_id else "",
        },
        {
            "key": "costing",
            "label": "Costing",
            "date": getattr(lifecycle.costing, "updated_at", None),
            "record": lifecycle.costing,
            "url_name": "cost_sheet_detail",
            "is_done": bool(lifecycle.costing_id),
            "amount": lifecycle.estimated_cost if lifecycle.costing_id else None,
            "notes": lifecycle.costing.get_status_display() if lifecycle.costing_id else "",
        },
        {
            "key": "quotation",
            "label": "Quotation",
            "date": getattr(lifecycle.quotation, "quoted_at", None),
            "record": lifecycle.quotation,
            "url_name": "cost_sheet_client_quotation",
            "is_done": bool(lifecycle.quotation_id),
            "amount": lifecycle.estimated_revenue if lifecycle.quotation_id else None,
            "notes": getattr(lifecycle.quotation, "quotation_number", "") if lifecycle.quotation_id else "",
        },
        {
            "key": "invoice",
            "label": "Invoice",
            "date": getattr(lifecycle.invoice, "issue_date", None),
            "record": lifecycle.invoice,
            "url_name": "invoice_view",
            "is_done": bool(lifecycle.invoice_id),
            "amount": _d(getattr(lifecycle.invoice, "total_amount", Decimal("0"))) if lifecycle.invoice_id else None,
            "notes": lifecycle.invoice.payment_status_label if lifecycle.invoice_id else "",
        },
        {
            "key": "production",
            "label": "Production",
            "date": getattr(lifecycle.production_order, "created_at", None),
            "record": lifecycle.production_order,
            "url_name": "production_detail",
            "is_done": bool(lifecycle.production_order_id),
            "amount": None,
            "notes": lifecycle.production_order.get_status_display() if lifecycle.production_order_id else "",
        },
        {
            "key": "shipping",
            "label": "Shipping",
            "date": getattr(lifecycle.shipping_record, "ship_date", None),
            "record": lifecycle.shipping_record,
            "url_name": "shipment_detail",
            "is_done": bool(lifecycle.shipping_record_id),
            "amount": None,
            "notes": lifecycle.shipping_record.get_status_display() if lifecycle.shipping_record_id else "",
        },
        {
            "key": "completed",
            "label": "Completed",
            "date": getattr(lifecycle.shipping_record, "delivered_at", None),
            "record": lifecycle.shipping_record,
            "url_name": "shipment_detail",
            "is_done": lifecycle.status == "completed",
            "amount": lifecycle.estimated_profit,
            "notes": "Delivered" if lifecycle.status == "completed" else "",
        },
    ]


def lifecycle_dashboard_metrics():
    lifecycles = OrderLifecycle.objects.select_related("invoice", "production_order", "shipping_record")
    active = lifecycles.exclude(status__in=["completed", "cancelled"])
    today = timezone.localdate()
    month_start = today.replace(day=1)

    total_invoice_value = Decimal("0")
    estimated_profit = Decimal("0")
    outstanding_balance = Decimal("0")
    margin_total = Decimal("0")
    margin_count = 0

    for lifecycle in lifecycles.iterator():
        total_invoice_value += _d(lifecycle.estimated_revenue)
        estimated_profit += _d(lifecycle.estimated_profit)
        if lifecycle.invoice_id:
            outstanding_balance += _d(lifecycle.invoice.balance)
        if lifecycle.estimated_revenue > 0:
            margin_total += _d(lifecycle.estimated_margin)
            margin_count += 1

    return {
        "active_orders": active.count(),
        "orders_in_costing": active.filter(status="costing").count(),
        "orders_waiting_quotation": active.filter(status="quotation").count(),
        "orders_waiting_payment": sum(1 for row in active if row.invoice_id and _d(row.invoice.balance) > 0),
        "orders_in_production": active.filter(status="production").count(),
        "orders_ready_to_ship": active.filter(status="production", production_order__status__in=["done", "closed_won"]).count(),
        "completed_this_month": lifecycles.filter(status="completed", updated_at__date__gte=month_start).count(),
        "total_invoice_value": _money(total_invoice_value),
        "estimated_profit": _money(estimated_profit),
        "average_margin": _money(margin_total / margin_count) if margin_count else Decimal("0.00"),
        "outstanding_balance": _money(outstanding_balance),
    }


def sync_lifecycle_for_invoice_payment(invoice, user=None):
    lifecycle = create_lifecycle_from_invoice(invoice, user=user)
    return lifecycle
