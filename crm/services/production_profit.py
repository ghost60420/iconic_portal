"""Read-only Production Profit Report calculations.

This service deliberately does not save or update any CRM record. Revenue comes
from non-cancelled invoices, except Bangladesh Local Sewing orders may use their
explicit quantity × sewing-charge fields when no invoice exists. Costs come
from explicit ProductionOrder fields, with linked BD accounting cost entries as
the documented Canada-export fallback.
"""

from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from crm.models import AccountingEntry, ExchangeRate, Invoice, ProductionOrder
from crm.services.costing_currency import CurrencyConversionError, convert_currency


ZERO = Decimal("0")
MONEY = Decimal("0.01")
PERCENT = Decimal("0.01")
CANADA_EXPORT_TYPES = {"fob", "canada_full"}
SEWING_SUBTYPES = {"sewing", "sewing charge", "swing", "swing charge"}
COST_MAIN_TYPES = {"COGS", "EXPENSE"}


def _decimal(value):
    if value in (None, ""):
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def _money(value):
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _margin(profit, revenue):
    revenue = _decimal(revenue)
    if revenue <= 0 or profit is None:
        return None
    return ((_decimal(profit) / revenue) * Decimal("100")).quantize(
        PERCENT,
        rounding=ROUND_HALF_UP,
    )


def _period_filter(field_name, year, month):
    filters = {f"{field_name}__year": year}
    if month:
        filters[f"{field_name}__month"] = month
    return filters


def _invoice_classification(invoice):
    market = (invoice.get("invoice_market") or "").lower().strip()
    region = (invoice.get("invoice_region") or "").upper().strip()
    currency = (invoice.get("currency") or "").upper().strip()
    if market == "bangladesh" or region == "BD":
        return "bangladesh_local"
    if market == "north_america" or region == "CA":
        return "canada_export"
    if currency == "BDT":
        return "bangladesh_local"
    if currency == "CAD":
        return "canada_export"
    return "unclassified"


def _order_classification(order, invoice_rows, accounting_rows):
    signals = set()
    if order.order_type == "sewing_charge" and order.factory_location == "bd":
        signals.add("bangladesh_local")
    elif order.order_type in CANADA_EXPORT_TYPES:
        signals.add("canada_export")

    for invoice in invoice_rows:
        classification = _invoice_classification(invoice)
        if classification != "unclassified":
            signals.add(classification)

    for entry in accounting_rows:
        side = (entry.get("side") or "").upper().strip()
        direction = (entry.get("direction") or "").upper().strip()
        if direction == "IN" and side == "BD":
            signals.add("bangladesh_local")
        elif direction == "IN" and side == "CA":
            signals.add("canada_export")

    return next(iter(signals)) if len(signals) == 1 else "unclassified"


def _invoice_revenue(invoice_rows):
    totals = defaultdict(lambda: ZERO)
    for invoice in invoice_rows:
        amount = _decimal(invoice.get("total_amount"))
        currency = (invoice.get("currency") or "").upper().strip()
        if amount > 0 and currency:
            totals[currency] += amount
    if len(totals) != 1:
        return None, None
    currency, amount = next(iter(totals.items()))
    return currency, _money(amount)


def _linked_bd_costs(accounting_rows):
    total_cost = ZERO
    sewing_cost = ZERO
    for entry in accounting_rows:
        side = (entry.get("side") or "").upper().strip()
        direction = (entry.get("direction") or "").upper().strip()
        main_type = (entry.get("main_type") or "").upper().strip()
        if side != "BD" or direction != "OUT" or main_type not in COST_MAIN_TYPES:
            continue
        amount_bdt = _decimal(entry.get("amount_bdt"))
        if amount_bdt <= 0 and (entry.get("currency") or "").upper().strip() == "BDT":
            amount_bdt = _decimal(entry.get("amount_original"))
        if amount_bdt <= 0:
            continue
        total_cost += amount_bdt
        subtype = (entry.get("sub_type") or "").lower().strip()
        if subtype in SEWING_SUBTYPES:
            sewing_cost += amount_bdt
    return _money(total_cost), _money(sewing_cost)


def _linked_canada_sewing_revenue(accounting_rows):
    revenue = ZERO
    for entry in accounting_rows:
        side = (entry.get("side") or "").upper().strip()
        direction = (entry.get("direction") or "").upper().strip()
        subtype = (entry.get("sub_type") or "").lower().strip()
        if side != "CA" or direction != "IN" or subtype not in SEWING_SUBTYPES:
            continue
        amount_cad = _decimal(entry.get("amount_cad"))
        if amount_cad <= 0 and (entry.get("currency") or "").upper().strip() == "CAD":
            amount_cad = _decimal(entry.get("amount_original"))
        if amount_cad > 0:
            revenue += amount_cad
    return _money(revenue) if revenue > 0 else None


def _canada_sewing_revenue(invoice_rows, accounting_rows):
    sewing_invoices = [
        invoice
        for invoice in invoice_rows
        if (invoice.get("invoice_type") or "").lower().strip() == "sewing_charge"
    ]
    currency, amount = _invoice_revenue(sewing_invoices)
    if amount is not None:
        return currency, amount, "Sewing charge invoice"
    if any(_decimal(invoice.get("total_amount")) > 0 for invoice in sewing_invoices):
        return None, None, "Unavailable"
    accounting_revenue = _linked_canada_sewing_revenue(accounting_rows)
    if accounting_revenue is not None:
        return "CAD", accounting_revenue, "CA accounting sewing revenue"
    return None, None, "Unavailable"


def _local_revenue(order, invoice_rows):
    currency, amount = _invoice_revenue(invoice_rows)
    if amount is not None:
        return currency, amount, "Invoice total"
    if any(_decimal(invoice.get("total_amount")) > 0 for invoice in invoice_rows):
        return None, None, "Unavailable"
    quantity = max(int(order.qty_total or 0), 0)
    charge = _decimal(order.sewing_charge_per_piece_bdt)
    if quantity > 0 and charge > 0:
        return "BDT", _money(Decimal(quantity) * charge), "Production sewing charge"
    return None, None, "Unavailable"


def _local_cost(order):
    quantity = max(int(order.qty_total or 0), 0)
    cost_per_piece = _decimal(order.sewing_cost_per_piece_bdt)
    if quantity <= 0 or cost_per_piece <= 0:
        return None, "Unavailable"
    cost = Decimal(quantity) * cost_per_piece + max(_decimal(order.extra_local_cost_bdt), ZERO)
    return _money(cost), "Production sewing cost"


def _canada_cost(order, accounting_rows):
    explicit = _decimal(order.actual_total_cost_bdt) or _decimal(order.production_total_cost_bdt)
    linked_total, _linked_sewing = _linked_bd_costs(accounting_rows)
    if explicit > 0:
        return _money(explicit), "Production total cost"
    if linked_total > 0:
        return linked_total, "BD accounting cost"
    return None, "Unavailable"


def _canada_sewing_cost(order, accounting_rows):
    explicit = _decimal(order.production_sewing_cost_bdt)
    _linked_total, linked_sewing = _linked_bd_costs(accounting_rows)
    if explicit > 0:
        return _money(explicit), "Production sewing cost"
    if linked_sewing > 0:
        return linked_sewing, "BD accounting sewing cost"
    return None, "Unavailable"


def _convert_bdt_to_cad(value, rate):
    if value is None or rate is None or rate <= 1:
        return None
    try:
        return _money(convert_currency(value, "BDT", "CAD", bdt_per_cad=rate))
    except CurrencyConversionError:
        return None


def _row_status(classification, revenue, cost_bdt, cost_cad, rate):
    if classification == "unclassified":
        return "Unavailable"
    if revenue is None:
        return "Missing revenue"
    if cost_bdt is None:
        return "Missing cost"
    if classification == "canada_export" and (rate is None or cost_cad is None):
        return "Missing exchange rate"
    return "Complete"


def _build_order_row(order, invoice_rows, accounting_rows, rate):
    classification = _order_classification(order, invoice_rows, accounting_rows)
    has_sewing_invoice = any(
        (invoice.get("invoice_type") or "").lower().strip() == "sewing_charge"
        for invoice in invoice_rows
    )
    has_canada_sewing_revenue = any(
        (entry.get("side") or "").upper().strip() == "CA"
        and (entry.get("direction") or "").upper().strip() == "IN"
        and (entry.get("sub_type") or "").lower().strip() in SEWING_SUBTYPES
        for entry in accounting_rows
    )
    is_sewing_charge = (
        order.order_type == "sewing_charge"
        or has_sewing_invoice
        or has_canada_sewing_revenue
    )

    if classification == "bangladesh_local":
        revenue_currency, revenue, revenue_source = _local_revenue(order, invoice_rows)
        cost_bdt, cost_source = _local_cost(order)
        cost_cad = _convert_bdt_to_cad(cost_bdt, rate)
        profit = _money(revenue - cost_bdt) if revenue is not None and cost_bdt is not None and revenue_currency == "BDT" else None
        profit_currency = "BDT" if profit is not None else None
        sewing_cost_bdt = cost_bdt if is_sewing_charge else None
        sewing_cost_cad = cost_cad if is_sewing_charge else None
        sewing_charge_currency = revenue_currency if is_sewing_charge else None
        sewing_charge_amount = revenue if is_sewing_charge else None
        sewing_charge_source = revenue_source if is_sewing_charge else "Unavailable"
    elif classification == "canada_export":
        revenue_currency, revenue = _invoice_revenue(invoice_rows)
        revenue_source = "Invoice total" if revenue is not None else "Unavailable"
        cost_bdt, cost_source = _canada_cost(order, accounting_rows)
        cost_cad = _convert_bdt_to_cad(cost_bdt, rate)
        profit = _money(revenue - cost_cad) if revenue is not None and cost_cad is not None and revenue_currency == "CAD" else None
        profit_currency = "CAD" if profit is not None else None
        sewing_cost_bdt, _sewing_cost_source = _canada_sewing_cost(order, accounting_rows)
        sewing_cost_cad = _convert_bdt_to_cad(sewing_cost_bdt, rate)
        (
            sewing_charge_currency,
            sewing_charge_amount,
            sewing_charge_source,
        ) = _canada_sewing_revenue(invoice_rows, accounting_rows)
    else:
        revenue_currency, revenue = _invoice_revenue(invoice_rows)
        revenue_source = "Invoice total" if revenue is not None else "Unavailable"
        cost_bdt = None
        cost_cad = None
        cost_source = "Unavailable"
        sewing_cost_bdt = None
        sewing_cost_cad = None
        sewing_charge_currency = None
        sewing_charge_amount = None
        sewing_charge_source = "Unavailable"
        profit = None
        profit_currency = None

    expected_currency = "BDT" if classification == "bangladesh_local" else "CAD"
    if revenue is not None and revenue_currency != expected_currency:
        classification = "unclassified"
        profit = None
        profit_currency = None

    status = _row_status(classification, revenue, cost_bdt, cost_cad, rate)
    margin = _margin(profit, revenue) if status == "Complete" else None
    customer = order.customer
    client = (
        (getattr(customer, "account_brand", "") if customer else "")
        or (getattr(customer, "contact_name", "") if customer else "")
        or order.brand_name_snapshot
        or order.client_name_snapshot
        or "Unavailable"
    )
    product = (
        (getattr(order.product, "name", "") if order.product else "")
        or order.product_name_snapshot
        or order.style_name
        or order.title
        or "Unavailable"
    )
    classification_label = {
        "canada_export": "Canada Export",
        "bangladesh_local": "Bangladesh Local",
    }.get(classification, "Unclassified")

    return {
        "production_order_id": order.pk,
        "purchase_order_number": order.purchase_order_number,
        "internal_order_id": order.internal_order_id,
        "client": client,
        "product": product,
        "quantity": order.qty_total or 0,
        "order_type": order.order_type,
        "order_type_label": order.get_order_type_display(),
        "classification": classification,
        "classification_label": classification_label,
        "is_sewing_charge": is_sewing_charge,
        "revenue_currency": revenue_currency,
        "revenue_amount": revenue,
        "revenue_source": revenue_source,
        "sewing_charge_currency": sewing_charge_currency,
        "sewing_charge_amount": sewing_charge_amount,
        "sewing_charge_source": sewing_charge_source,
        "sewing_cost_bdt": sewing_cost_bdt,
        "sewing_cost_cad": sewing_cost_cad,
        "cost_bdt": cost_bdt,
        "cost_cad": cost_cad,
        "cost_source": cost_source,
        "profit": profit,
        "profit_currency": profit_currency,
        "margin_pct": margin,
        "data_status": status,
    }


def _summary(
    rows,
    classification,
    currency,
    *,
    sewing_only=False,
    sewing_cost=False,
    sewing_revenue=False,
):
    selected = [
        row for row in rows
        if row["classification"] == classification
        and (not sewing_only or row["is_sewing_charge"])
    ]
    revenue_key = "sewing_charge_amount" if sewing_revenue else "revenue_amount"
    revenue_currency_key = "sewing_charge_currency" if sewing_revenue else "revenue_currency"
    revenue_complete = bool(selected) and all(
        row[revenue_key] is not None and row[revenue_currency_key] == currency
        for row in selected
    )
    cost_key = "sewing_cost_bdt" if sewing_cost else "cost_bdt"
    cad_cost_key = "sewing_cost_cad" if sewing_cost else "cost_cad"
    cost_complete = bool(selected) and all(row[cost_key] is not None for row in selected)
    rate_complete = classification != "canada_export" or all(row[cad_cost_key] is not None for row in selected)
    complete = revenue_complete and cost_complete and rate_complete
    revenue = _money(sum((row[revenue_key] for row in selected), ZERO)) if revenue_complete else None
    cost_bdt = _money(sum((row[cost_key] for row in selected), ZERO)) if cost_complete else None
    if classification == "canada_export":
        cost_cad = _money(sum((row[cad_cost_key] for row in selected), ZERO)) if complete else None
        profit = _money(revenue - cost_cad) if complete else None
    else:
        cost_cad = None
        profit = _money(revenue - cost_bdt) if complete else None
    return {
        "order_count": len(selected),
        "complete": complete,
        "revenue": revenue,
        "revenue_currency": currency,
        "cost_bdt": cost_bdt,
        "cost_cad": cost_cad,
        "profit": profit,
        "profit_currency": currency if profit is not None else None,
        "margin_pct": _margin(profit, revenue) if complete else None,
    }


def build_production_profit_report(*, year, month=None, search_query=""):
    """Return report rows and summaries without writing to any model."""
    invoice_qs = (
        Invoice.objects.filter(
            order_id__isnull=False,
            is_archived=False,
            **_period_filter("issue_date", year, month),
        )
        .exclude(status="cancelled")
        .values(
            "order_id",
            "currency",
            "invoice_region",
            "invoice_market",
            "invoice_type",
            "total_amount",
        )
    )
    accounting_qs = AccountingEntry.objects.filter(
        production_order_id__isnull=False,
        **_period_filter("date", year, month),
    ).values(
        "production_order_id",
        "side",
        "direction",
        "main_type",
        "sub_type",
        "currency",
        "amount_original",
        "amount_cad",
        "amount_bdt",
    )
    period_order_qs = ProductionOrder.objects.filter(
        is_archived=False,
        **_period_filter("created_at", year, month),
    )
    matching_order_ids = None
    if search_query:
        matching_order_ids = set(ProductionOrder.objects.filter(is_archived=False).filter(
            ProductionOrder.identifier_search_query(search_query)
            | Q(title__icontains=search_query)
            | Q(client_name_snapshot__icontains=search_query)
            | Q(brand_name_snapshot__icontains=search_query)
            | Q(product_name_snapshot__icontains=search_query)
        ).values_list("id", flat=True))

    invoices = list(invoice_qs)
    accounting = list(accounting_qs)
    order_ids = set(period_order_qs.values_list("id", flat=True))
    order_ids.update(row["order_id"] for row in invoices if row["order_id"])
    order_ids.update(row["production_order_id"] for row in accounting if row["production_order_id"])
    if matching_order_ids is not None:
        order_ids.intersection_update(matching_order_ids)

    invoices_by_order = defaultdict(list)
    for invoice in invoices:
        invoices_by_order[invoice["order_id"]].append(invoice)
    accounting_by_order = defaultdict(list)
    for entry in accounting:
        accounting_by_order[entry["production_order_id"]].append(entry)

    rate_row = ExchangeRate.objects.order_by("-updated_at", "-id").only("cad_to_bdt").first()
    rate = _decimal(rate_row.cad_to_bdt) if rate_row and rate_row.cad_to_bdt and rate_row.cad_to_bdt > 1 else None
    orders = (
        ProductionOrder.objects.filter(pk__in=order_ids, is_archived=False)
        .select_related("customer", "product")
        .order_by("-created_at", "-id")
    )
    rows = [
        _build_order_row(
            order,
            invoices_by_order[order.pk],
            accounting_by_order[order.pk],
            rate,
        )
        for order in orders
    ]

    canada = _summary(rows, "canada_export", "CAD")
    local = _summary(rows, "bangladesh_local", "BDT")
    local_sewing = _summary(
        rows,
        "bangladesh_local",
        "BDT",
        sewing_only=True,
        sewing_cost=True,
        sewing_revenue=True,
    )
    canada_sewing = _summary(
        rows,
        "canada_export",
        "CAD",
        sewing_only=True,
        sewing_cost=True,
        sewing_revenue=True,
    )
    classified = [row for row in rows if row["classification"] != "unclassified"]
    combined_complete = bool(classified) and rate is not None and all(row["data_status"] == "Complete" for row in classified)
    combined_revenue = None
    combined_cost = None
    combined_profit = None
    if combined_complete:
        combined_revenue = _money(sum(
            (
                row["revenue_amount"]
                if row["revenue_currency"] == "CAD"
                else convert_currency(row["revenue_amount"], "BDT", "CAD", bdt_per_cad=rate)
            )
            for row in classified
        ))
        combined_cost = _money(sum((row["cost_cad"] for row in classified), ZERO))
        combined_profit = _money(combined_revenue - combined_cost)

    return {
        "rows": rows,
        "exchange_rate": rate,
        "canada_export": canada,
        "bangladesh_local": local,
        "bangladesh_local_sewing": local_sewing,
        "canada_export_sewing": canada_sewing,
        "combined": {
            "complete": combined_complete,
            "revenue_cad": combined_revenue,
            "cost_cad": combined_cost,
            "profit_cad": combined_profit,
            "margin_pct": _margin(combined_profit, combined_revenue) if combined_complete else None,
        },
        "status_counts": Counter(row["data_status"] for row in rows),
        "unclassified_count": sum(1 for row in rows if row["classification"] == "unclassified"),
    }
