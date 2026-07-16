"""Read-only Production Profit Report calculations.

This service deliberately does not save or update any CRM record. Sample invoice
revenue is reported separately and excluded from every production margin.
Production revenue comes from non-cancelled, non-sample invoices, except
Bangladesh Local Sewing orders may use their explicit quantity × sewing-charge
fields when no invoice exists. Costs come from explicit ProductionOrder fields,
with linked BD accounting cost entries as the documented Canada-export fallback.
"""

import re
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from crm.models import AccountingEntry, ExchangeRate, Invoice, ProductionOrder
from crm.services.costing_currency import CurrencyConversionError, convert_currency
from crm.services.historical_dates import INVOICE_REPORTING_DATE_ALIAS, with_invoice_reporting_date


ZERO = Decimal("0")
MONEY = Decimal("0.01")
PERCENT = Decimal("0.01")
CANADA_EXPORT_TYPES = {"fob", "canada_full"}
SEWING_SUBTYPES = {"sewing", "sewing charge", "swing", "swing charge"}
COST_MAIN_TYPES = {"COGS", "EXPENSE"}
SAMPLE_TEXT_PATTERN = re.compile(r"(^|\W)sample(s|d|ing)?($|\W)", re.IGNORECASE)
REVENUE_TYPES = ("bulk", "sewing", "sample", "other")
OTHER_REVENUE_PATTERNS = (
    ("Design", ("design fee", "design service", "design income", "design")),
    ("Pattern", ("pattern fee", "pattern service", "pattern making", "pattern")),
    ("Tech Pack", ("tech pack", "technical pack")),
    ("Shipping Income", ("shipping income", "shipping fee", "courier income", "freight income", "shipping", "courier", "freight")),
    ("Consulting", ("consulting", "consultancy")),
    ("Other Service Fees", ("other service fee", "service fee")),
)


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


def _date_filter(field_name, year, month, start_date=None, end_date=None):
    if start_date or end_date:
        filters = {}
        if start_date:
            filters[f"{field_name}__gte"] = start_date
        if end_date:
            filters[f"{field_name}__lte"] = end_date
        return filters
    return _period_filter(field_name, year, month)


def classify_other_revenue_text(*values):
    text = " ".join(str(value or "").strip().lower() for value in values)
    for label, phrases in OTHER_REVENUE_PATTERNS:
        if any(
            re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text)
            for phrase in phrases
        ):
            return label
    return None


def _contains_sample(value):
    return bool(SAMPLE_TEXT_PATTERN.search(str(value or "").strip()))


def _sample_classification(invoice):
    """Return sample, production, or unclassified without guessing.

    Persisted invoice/costing/production type fields are authoritative. A
    product/service descriptor containing the standalone word "sample" is also
    accepted by the report requirement. A sewing-charge invoice carrying a
    contradictory sample signal is kept out of both report categories.
    """
    invoice_type = (invoice.get("invoice_type") or "").lower().strip()
    signals = []
    if invoice_type == "sample":
        signals.append("Sample invoice")
    if (invoice.get("quick_costing__costing_purpose") or "").lower().strip() == "sample":
        signals.append("Quick Costing sample")
    if (invoice.get("order__production_order_type") or "").lower().strip() == "sampling":
        signals.append("Production sample")

    descriptor_fields = (
        "order__title",
        "order__style_name",
        "order__product_name_snapshot",
        "order__product_type_snapshot",
        "quick_costing__project_name",
        "quick_costing__product_type",
        "costing_header__style_name",
        "costing_header__product_type",
    )
    if any(_contains_sample(invoice.get(field)) for field in descriptor_fields):
        signals.append("Sample product/service")

    if not signals:
        return "production", ""
    if invoice_type == "sewing_charge":
        return "unclassified", "Conflicting sample and sewing-charge classification"
    return "sample", " · ".join(dict.fromkeys(signals))


def classify_invoice_revenue_type(invoice):
    """Return an exclusive revenue category using existing persisted fields."""
    sample_classification, sample_reason = _sample_classification(invoice)
    if sample_classification == "sample":
        return "sample", sample_reason
    if sample_classification == "unclassified":
        return "unclassified", sample_reason

    invoice_type = (invoice.get("invoice_type") or "").lower().strip()
    if invoice_type == "sewing_charge":
        return "sewing", "Sewing charge invoice"

    other_label = None
    if not invoice.get("order_id"):
        other_label = classify_other_revenue_text(
            invoice.get("notes"),
            invoice.get("quick_costing__project_name"),
            invoice.get("quick_costing__product_type"),
            invoice.get("costing_header__style_name"),
            invoice.get("costing_header__product_type"),
        )
    if other_label:
        return "other", other_label

    if (
        invoice_type == "bulk"
        or invoice.get("order_id")
        or (invoice.get("quick_costing__costing_purpose") or "").lower().strip() == "bulk"
    ):
        return "bulk", "Bulk production invoice"
    return "unclassified", "Revenue type unavailable"


def _sample_quantity(invoice):
    for field in (
        "order__qty_total",
        "quick_costing__quantity",
        "costing_header__order_quantity",
    ):
        value = invoice.get(field)
        if value not in (None, ""):
            try:
                quantity = int(value)
            except (TypeError, ValueError):
                continue
            if quantity > 0:
                return quantity
    return None


def _sample_lead_id(invoice):
    return (
        invoice.get("order__lead__lead_id")
        or invoice.get("order__opportunity__lead__lead_id")
        or invoice.get("quick_costing__opportunity__lead__lead_id")
        or invoice.get("costing_header__opportunity__lead__lead_id")
        or "Unavailable"
    )


def _sample_opportunity_id(invoice):
    return (
        invoice.get("order__opportunity__opportunity_id")
        or invoice.get("quick_costing__opportunity__opportunity_id")
        or invoice.get("costing_header__opportunity__opportunity_id")
        or "Unavailable"
    )


def _sample_client(invoice):
    return (
        invoice.get("customer__account_brand")
        or invoice.get("customer__contact_name")
        or invoice.get("order__customer__account_brand")
        or invoice.get("order__customer__contact_name")
        or invoice.get("quick_costing__buyer_name")
        or invoice.get("costing_header__buyer")
        or "Unavailable"
    )


def _invoice_brand(invoice):
    return (
        invoice.get("customer__account_brand")
        or invoice.get("order__brand_name_snapshot")
        or invoice.get("order__customer__account_brand")
        or invoice.get("quick_costing__account_brand")
        or invoice.get("costing_header__brand")
        or "Unavailable"
    )


def _invoice_country(invoice):
    return (
        invoice.get("customer__country")
        or invoice.get("order__customer__country")
        or invoice.get("order__lead__country")
        or invoice.get("quick_costing__opportunity__lead__country")
        or invoice.get("costing_header__opportunity__lead__country")
        or "Unavailable"
    )


def _sample_purchase_order(invoice):
    if not invoice.get("order_id"):
        return "Unavailable"
    return ProductionOrder.format_purchase_order_number(
        invoice.get("order__order_code"),
        invoice.get("order_id"),
    ) or "Unavailable"


def _sample_payment_status(total, paid):
    if paid <= 0:
        return "Unpaid"
    if total > 0 and paid > total:
        return "Overpaid"
    if total > 0 and paid >= total:
        return "Paid"
    return "Partially paid"


def _build_sample_row(invoice, sample_type):
    amount = _money(invoice.get("total_amount"))
    paid = _money(invoice.get("paid_amount"))
    balance = _money(amount - paid)
    recorded_cost = _money(
        _decimal(invoice.get("sewing_charge"))
        + _decimal(invoice.get("other_internal_cost"))
    )
    sample_cost = recorded_cost if recorded_cost > 0 else None
    gross_profit = _money(amount - sample_cost) if sample_cost is not None else None
    return {
        "invoice_id": invoice["id"],
        "invoice_number": invoice.get("invoice_number") or "Unavailable",
        "client": _sample_client(invoice),
        "brand": _invoice_brand(invoice),
        "country": _invoice_country(invoice),
        "lead_id": _sample_lead_id(invoice),
        "opportunity_id": _sample_opportunity_id(invoice),
        "production_order_id": invoice.get("order_id"),
        "purchase_order_number": _sample_purchase_order(invoice),
        "internal_order_id": invoice.get("order__order_code") or "",
        "sample_type": sample_type or "Sample",
        "pieces": _sample_quantity(invoice),
        "currency": (invoice.get("currency") or "").upper().strip() or "Unavailable",
        "invoice_amount": amount,
        "courier_charge": _money(invoice.get("shipping_amount")),
        "sample_cost": sample_cost,
        "gross_profit": gross_profit,
        "margin_pct": _margin(gross_profit, amount),
        "paid_amount": paid,
        "balance_amount": balance,
        "invoice_status": (invoice.get("status") or "Unavailable").replace("_", " ").title(),
        "payment_status": _sample_payment_status(amount, paid),
        "credit_status": "Credit tracking unavailable",
        "issue_date": invoice.get(INVOICE_REPORTING_DATE_ALIAS) or invoice.get("issue_date"),
        "revenue_type": "sample",
    }


def _sample_summaries(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["currency"]].append(row)

    summaries = []
    for currency in sorted(grouped):
        currency_rows = grouped[currency]
        pieces_available = all(row["pieces"] is not None for row in currency_rows)
        cost_available = all(row["sample_cost"] is not None for row in currency_rows)
        revenue = _money(sum((row["invoice_amount"] for row in currency_rows), ZERO))
        cost = (
            _money(sum((row["sample_cost"] for row in currency_rows), ZERO))
            if cost_available
            else None
        )
        profit = _money(revenue - cost) if cost is not None else None
        summaries.append({
            "currency": currency,
            "invoice_count": len(currency_rows),
            "piece_count": (
                sum(row["pieces"] for row in currency_rows)
                if pieces_available
                else None
            ),
            "revenue": revenue,
            "cost": cost,
            "cost_available": cost_available,
            "profit": profit,
            "margin_pct": _margin(profit, revenue),
            "paid": _money(sum((row["paid_amount"] for row in currency_rows), ZERO)),
            "balance": _money(sum((row["balance_amount"] for row in currency_rows), ZERO)),
        })
    return summaries


def _sample_matches_search(row, search_query):
    if not search_query:
        return True
    query = search_query.lower().strip()
    values = (
        row["invoice_number"],
        row["client"],
        row["lead_id"],
        row["opportunity_id"],
        row["purchase_order_number"],
        row["internal_order_id"],
    )
    return any(query in str(value or "").lower() for value in values)


def _matches_breakdown_filters(row, *, client="", brand="", country=""):
    checks = (
        (client, row.get("client")),
        (brand, row.get("brand")),
        (country, row.get("country")),
    )
    return all(
        not needle or needle.lower().strip() in str(value or "").lower()
        for needle, value in checks
    )


def _matches_breakdown_search(row, search_query):
    if not search_query:
        return True
    query = search_query.lower().strip()
    return any(
        query in str(row.get(field) or "").lower()
        for field in (
            "reference",
            "client",
            "brand",
            "country",
            "service_type",
            "purchase_order_number",
            "internal_order_id",
        )
    )
def _build_other_invoice_row(invoice, service_type):
    amount = _money(invoice.get("total_amount"))
    paid = _money(invoice.get("paid_amount"))
    return {
        "source": "Invoice",
        "source_id": invoice["id"],
        "reference": invoice.get("invoice_number") or "Unavailable",
        "date": invoice.get(INVOICE_REPORTING_DATE_ALIAS) or invoice.get("issue_date"),
        "service_type": service_type,
        "client": _sample_client(invoice),
        "brand": _invoice_brand(invoice),
        "country": _invoice_country(invoice),
        "currency": (invoice.get("currency") or "").upper().strip() or "Unavailable",
        "revenue": amount,
        "cost": None,
        "profit": None,
        "margin_pct": None,
        "paid_amount": paid,
        "balance_amount": _money(amount - paid),
        "payment_status": _sample_payment_status(amount, paid),
        "revenue_type": "other",
    }


def _build_unlinked_production_invoice_row(invoice, revenue_type):
    amount = _money(invoice.get("total_amount"))
    paid = _money(invoice.get("paid_amount"))
    return {
        "source": "Invoice",
        "source_id": invoice["id"],
        "reference": invoice.get("invoice_number") or "Unavailable",
        "date": invoice.get(INVOICE_REPORTING_DATE_ALIAS) or invoice.get("issue_date"),
        "client": _sample_client(invoice),
        "brand": _invoice_brand(invoice),
        "country": _invoice_country(invoice),
        "currency": (invoice.get("currency") or "").upper().strip() or "Unavailable",
        "revenue": amount,
        "cost": None,
        "profit": None,
        "margin_pct": None,
        "paid_amount": paid,
        "balance_amount": _money(amount - paid),
        "payment_status": _sample_payment_status(amount, paid),
        "revenue_type": revenue_type,
    }


def _accounting_revenue_entry(entry):
    direction = (entry.get("direction") or "").upper().strip()
    main_type = (entry.get("main_type") or "").upper().strip()
    status = (entry.get("status") or "").upper().strip()
    return (
        direction == "IN"
        and main_type in {"INCOME", "REVENUE"}
        and status not in {"CANCELLED", "VOID"}
    )


def _accounting_client(entry):
    return (
        entry.get("customer__account_brand")
        or entry.get("customer__contact_name")
        or entry.get("production_order__customer__account_brand")
        or entry.get("production_order__customer__contact_name")
        or "Unavailable"
    )


def _accounting_brand(entry):
    return (
        entry.get("customer__account_brand")
        or entry.get("production_order__brand_name_snapshot")
        or entry.get("production_order__customer__account_brand")
        or "Unavailable"
    )


def _accounting_country(entry):
    return (
        entry.get("customer__country")
        or entry.get("production_order__customer__country")
        or entry.get("opportunity__lead__country")
        or "Unavailable"
    )


def _build_other_accounting_row(entry):
    if not _accounting_revenue_entry(entry):
        return None
    subtype = (entry.get("sub_type") or "").strip()
    if subtype.lower() == "invoice payment received":
        return None
    service_type = classify_other_revenue_text(
        subtype,
        entry.get("description"),
        entry.get("internal_note"),
    )
    if not service_type:
        return None
    amount = _money(entry.get("amount_original"))
    if amount <= 0:
        return None
    return {
        "source": "Accounting Entry",
        "source_id": entry["id"],
        "reference": f"Accounting #{entry['id']}",
        "date": entry.get("date"),
        "service_type": service_type,
        "client": _accounting_client(entry),
        "brand": _accounting_brand(entry),
        "country": _accounting_country(entry),
        "currency": (entry.get("currency") or "").upper().strip() or "Unavailable",
        "revenue": amount,
        "cost": None,
        "profit": None,
        "margin_pct": None,
        "paid_amount": amount,
        "balance_amount": ZERO,
        "payment_status": "Received",
        "revenue_type": "other",
    }


def _production_category_summaries(rows, *, sewing, unlinked_rows=None):
    grouped = defaultdict(list)
    for row in rows:
        if row["classification"] == "unclassified" or row["is_sewing_charge"] != sewing:
            continue
        revenue = row["sewing_charge_amount"] if sewing else row["revenue_amount"]
        currency = row["sewing_charge_currency"] if sewing else row["revenue_currency"]
        if revenue is None or not currency:
            continue
        cost = None
        if currency == "CAD":
            cost = row["sewing_cost_cad"] if sewing else row["cost_cad"]
        elif currency == "BDT":
            cost = row["sewing_cost_bdt"] if sewing else row["cost_bdt"]
        grouped[currency].append((revenue, cost))

    expected_type = "sewing" if sewing else "bulk"
    for row in unlinked_rows or []:
        if row["revenue_type"] == expected_type and row["revenue"] > 0:
            grouped[row["currency"]].append((row["revenue"], None))

    summaries = []
    for currency in sorted(grouped):
        values = grouped[currency]
        revenue = _money(sum((item[0] for item in values), ZERO))
        cost_available = all(item[1] is not None for item in values)
        cost = _money(sum((item[1] for item in values), ZERO)) if cost_available else None
        profit = _money(revenue - cost) if cost is not None else None
        summaries.append({
            "currency": currency,
            "order_count": len(values),
            "revenue": revenue,
            "cost": cost,
            "cost_available": cost_available,
            "profit": profit,
            "margin_pct": _margin(profit, revenue),
        })
    return summaries


def _other_revenue_summaries(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["currency"]].append(row)
    return [
        {
            "currency": currency,
            "record_count": len(grouped[currency]),
            "revenue": _money(sum((row["revenue"] for row in grouped[currency]), ZERO)),
            "cost": None,
            "cost_available": False,
            "profit": None,
            "margin_pct": None,
        }
        for currency in sorted(grouped)
    ]


def _company_revenue_summaries(category_summaries):
    currencies = sorted({
        row["currency"]
        for summaries in category_summaries.values()
        for row in summaries
    })
    company_rows = []
    for currency in currencies:
        category_rows = [
            row
            for summaries in category_summaries.values()
            for row in summaries
            if row["currency"] == currency and row["revenue"] > 0
        ]
        revenue = _money(sum((row["revenue"] for row in category_rows), ZERO))
        cost_available = bool(category_rows) and all(row["cost_available"] for row in category_rows)
        cost = (
            _money(sum((row["cost"] for row in category_rows), ZERO))
            if cost_available
            else None
        )
        profit = _money(revenue - cost) if cost is not None else None
        company_rows.append({
            "currency": currency,
            "revenue": revenue,
            "cost": cost,
            "cost_available": cost_available,
            "profit": profit,
            "margin_pct": _margin(profit, revenue),
        })
    return company_rows


def _accounting_reconciliation(entries, company_rows, *, client="", brand="", country=""):
    accounting = defaultdict(lambda: ZERO)
    for entry in entries:
        filter_row = {
            "client": _accounting_client(entry),
            "brand": _accounting_brand(entry),
            "country": _accounting_country(entry),
        }
        if not _accounting_revenue_entry(entry) or not _matches_breakdown_filters(
            filter_row,
            client=client,
            brand=brand,
            country=country,
        ):
            continue
        currency = (entry.get("currency") or "").upper().strip()
        if currency:
            accounting[currency] += _decimal(entry.get("amount_original"))

    categorized = {row["currency"]: row["revenue"] for row in company_rows}
    currencies = sorted(set(accounting) | set(categorized))
    return [
        {
            "currency": currency,
            "categorized_revenue": _money(categorized.get(currency, ZERO)),
            "accounting_revenue": _money(accounting.get(currency, ZERO)),
            "difference": _money(categorized.get(currency, ZERO) - accounting.get(currency, ZERO)),
        }
        for currency in currencies
    ]


def _revenue_export_rows(
    production_rows,
    sample_rows,
    other_rows,
    unlinked_rows,
    revenue_type="",
):
    export_rows = []
    for row in production_rows:
        row_type = "sewing" if row["is_sewing_charge"] else "bulk"
        if revenue_type and row_type != revenue_type:
            continue
        revenue = row["sewing_charge_amount"] if row_type == "sewing" else row["revenue_amount"]
        currency = row["sewing_charge_currency"] if row_type == "sewing" else row["revenue_currency"]
        if currency == "CAD":
            cost = row["sewing_cost_cad"] if row_type == "sewing" else row["cost_cad"]
        elif currency == "BDT":
            cost = row["sewing_cost_bdt"] if row_type == "sewing" else row["cost_bdt"]
        else:
            cost = None
        profit = _money(revenue - cost) if revenue is not None and cost is not None else None
        export_rows.append({
            "revenue_type": row_type.title(),
            "subtype": row["classification_label"],
            "date": row["date"],
            "reference": row["purchase_order_number"],
            "client": row["client"],
            "brand": row["brand"],
            "country": row["country"],
            "currency": currency,
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "margin_pct": _margin(profit, revenue),
            "paid": None,
            "balance": None,
            "status": row["data_status"],
        })

    for row in unlinked_rows:
        if revenue_type and row["revenue_type"] != revenue_type:
            continue
        export_rows.append({
            "revenue_type": row["revenue_type"].title(),
            "subtype": "Unlinked invoice",
            "date": row["date"],
            "reference": row["reference"],
            "client": row["client"],
            "brand": row["brand"],
            "country": row["country"],
            "currency": row["currency"],
            "revenue": row["revenue"],
            "cost": None,
            "profit": None,
            "margin_pct": None,
            "paid": row["paid_amount"],
            "balance": row["balance_amount"],
            "status": row["payment_status"],
        })

    if not revenue_type or revenue_type == "sample":
        for row in sample_rows:
            export_rows.append({
                "revenue_type": "Sample",
                "subtype": row["sample_type"],
                "date": row["issue_date"],
                "reference": row["invoice_number"],
                "client": row["client"],
                "brand": row["brand"],
                "country": row["country"],
                "currency": row["currency"],
                "revenue": row["invoice_amount"],
                "cost": row["sample_cost"],
                "profit": row["gross_profit"],
                "margin_pct": row["margin_pct"],
                "paid": row["paid_amount"],
                "balance": row["balance_amount"],
                "status": row["payment_status"],
            })

    if not revenue_type or revenue_type == "other":
        for row in other_rows:
            export_rows.append({
                "revenue_type": "Other",
                "subtype": row["service_type"],
                "date": row["date"],
                "reference": row["reference"],
                "client": row["client"],
                "brand": row["brand"],
                "country": row["country"],
                "currency": row["currency"],
                "revenue": row["revenue"],
                "cost": None,
                "profit": None,
                "margin_pct": None,
                "paid": row["paid_amount"],
                "balance": row["balance_amount"],
                "status": row["payment_status"],
            })
    return sorted(
        export_rows,
        key=lambda row: (str(row["date"] or ""), row["reference"]),
        reverse=True,
    )


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
    lead = getattr(order, "lead", None)
    client = (
        (getattr(customer, "account_brand", "") if customer else "")
        or (getattr(customer, "contact_name", "") if customer else "")
        or order.brand_name_snapshot
        or order.client_name_snapshot
        or "Unavailable"
    )
    brand = (
        (getattr(customer, "account_brand", "") if customer else "")
        or order.brand_name_snapshot
        or "Unavailable"
    )
    country = (
        (getattr(customer, "country", "") if customer else "")
        or (getattr(lead, "country", "") if lead else "")
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
        "brand": brand,
        "country": country,
        "date": order.created_at.date() if order.created_at else None,
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


def build_production_profit_report(
    *,
    year,
    month=None,
    search_query="",
    start_date=None,
    end_date=None,
    client="",
    brand="",
    country="",
    revenue_type="",
):
    """Return report rows and summaries without writing to any model."""
    if revenue_type not in {"", *REVENUE_TYPES}:
        revenue_type = ""
    invoice_qs = (
        with_invoice_reporting_date(Invoice.objects.filter(is_archived=False).exclude(status="cancelled"))
        .filter(**_date_filter(INVOICE_REPORTING_DATE_ALIAS, year, month, start_date, end_date))
        .values(
            "id",
            "invoice_number",
            "issue_date",
            "invoice_date",
            INVOICE_REPORTING_DATE_ALIAS,
            "order_id",
            "costing_header_id",
            "quick_costing_id",
            "currency",
            "invoice_region",
            "invoice_market",
            "invoice_type",
            "total_amount",
            "paid_amount",
            "status",
            "shipping_amount",
            "sewing_charge",
            "other_internal_cost",
            "notes",
            "customer__account_brand",
            "customer__contact_name",
            "customer__country",
            "order__order_code",
            "order__title",
            "order__style_name",
            "order__brand_name_snapshot",
            "order__product_name_snapshot",
            "order__product_type_snapshot",
            "order__production_order_type",
            "order__qty_total",
            "order__customer__account_brand",
            "order__customer__contact_name",
            "order__customer__country",
            "order__lead__lead_id",
            "order__lead__country",
            "order__opportunity__opportunity_id",
            "order__opportunity__lead__lead_id",
            "quick_costing__costing_purpose",
            "quick_costing__quantity",
            "quick_costing__account_brand",
            "quick_costing__project_name",
            "quick_costing__product_type",
            "quick_costing__buyer_name",
            "quick_costing__opportunity__opportunity_id",
            "quick_costing__opportunity__lead__lead_id",
            "quick_costing__opportunity__lead__country",
            "costing_header__order_quantity",
            "costing_header__style_name",
            "costing_header__product_type",
            "costing_header__buyer",
            "costing_header__brand",
            "costing_header__opportunity__opportunity_id",
            "costing_header__opportunity__lead__lead_id",
            "costing_header__opportunity__lead__country",
        )
    )
    accounting_qs = AccountingEntry.objects.filter(
        **_date_filter("date", year, month, start_date, end_date),
    ).values(
        "id",
        "date",
        "production_order_id",
        "side",
        "direction",
        "status",
        "main_type",
        "sub_type",
        "currency",
        "amount_original",
        "amount_cad",
        "amount_bdt",
        "description",
        "internal_note",
        "customer__account_brand",
        "customer__contact_name",
        "customer__country",
        "opportunity__lead__country",
        "production_order__brand_name_snapshot",
        "production_order__customer__account_brand",
        "production_order__customer__contact_name",
        "production_order__customer__country",
    )
    period_order_qs = ProductionOrder.objects.filter(
        is_archived=False,
        **_date_filter("created_at__date", year, month, start_date, end_date),
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

    all_invoices = list(invoice_qs)
    invoices = []
    sample_rows = []
    other_invoice_rows = []
    unlinked_production_invoice_rows = []
    unclassified_sample_invoices = []
    unclassified_revenue = []
    sample_or_uncertain_order_ids = set()
    for invoice in all_invoices:
        invoice_revenue_type, reason = classify_invoice_revenue_type(invoice)
        if invoice_revenue_type == "sample":
            if invoice.get("order_id"):
                sample_or_uncertain_order_ids.add(invoice["order_id"])
            row = _build_sample_row(invoice, reason)
            if (
                _sample_matches_search(row, search_query)
                and _matches_breakdown_filters(row, client=client, brand=brand, country=country)
            ):
                sample_rows.append(row)
        elif invoice_revenue_type == "other":
            row = _build_other_invoice_row(invoice, reason)
            if (
                _matches_breakdown_search(row, search_query)
                and _matches_breakdown_filters(row, client=client, brand=brand, country=country)
            ):
                other_invoice_rows.append(row)
        elif invoice_revenue_type in {"bulk", "sewing"} and not invoice.get("order_id"):
            row = _build_unlinked_production_invoice_row(invoice, invoice_revenue_type)
            if (
                _matches_breakdown_search(row, search_query)
                and _matches_breakdown_filters(row, client=client, brand=brand, country=country)
            ):
                unlinked_production_invoice_rows.append(row)
        elif invoice_revenue_type == "unclassified":
            if invoice.get("order_id"):
                sample_or_uncertain_order_ids.add(invoice["order_id"])
            unclassified_row = {
                "invoice_id": invoice["id"],
                "invoice_number": invoice.get("invoice_number") or "Unavailable",
                "reason": reason,
            }
            unclassified_revenue.append(unclassified_row)
            if "sample" in reason.lower():
                unclassified_sample_invoices.append(unclassified_row)
        elif invoice.get("order_id"):
            invoices.append(invoice)
    sample_rows.sort(key=lambda row: (row["issue_date"], row["invoice_id"]), reverse=True)
    all_accounting = list(accounting_qs)
    accounting = [row for row in all_accounting if row["production_order_id"]]
    other_accounting_rows = []
    for entry in all_accounting:
        row = _build_other_accounting_row(entry)
        if row and _matches_breakdown_search(row, search_query) and _matches_breakdown_filters(
            row,
            client=client,
            brand=brand,
            country=country,
        ):
            other_accounting_rows.append(row)
    order_ids = set(period_order_qs.values_list("id", flat=True))
    order_ids.update(row["order_id"] for row in invoices if row["order_id"])
    order_ids.update(row["production_order_id"] for row in accounting if row["production_order_id"])
    production_invoice_order_ids = {
        row["order_id"] for row in invoices if row["order_id"]
    }
    order_ids.difference_update(
        sample_or_uncertain_order_ids - production_invoice_order_ids
    )
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
        .exclude(production_order_type="sampling")
        .select_related("customer", "product", "lead")
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
    rows = [
        row for row in rows
        if _matches_breakdown_filters(row, client=client, brand=brand, country=country)
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

    other_rows = sorted(
        [*other_invoice_rows, *other_accounting_rows],
        key=lambda row: (row["date"], row["source_id"]),
        reverse=True,
    )
    category_summaries = {
        "bulk": _production_category_summaries(
            rows,
            sewing=False,
            unlinked_rows=unlinked_production_invoice_rows,
        ),
        "sewing": _production_category_summaries(
            rows,
            sewing=True,
            unlinked_rows=unlinked_production_invoice_rows,
        ),
        "sample": _sample_summaries(sample_rows),
        "other": _other_revenue_summaries(other_rows),
    }
    company_revenue = _company_revenue_summaries(category_summaries)
    export_rows = _revenue_export_rows(
        rows,
        sample_rows,
        other_rows,
        unlinked_production_invoice_rows,
        revenue_type,
    )

    return {
        "rows": rows,
        "revenue_type_filter": revenue_type,
        "revenue_type_options": [
            ("", "All Revenue"),
            ("bulk", "Bulk Production Revenue"),
            ("sewing", "Sewing Charge Revenue"),
            ("sample", "Sample Revenue"),
            ("other", "Other Revenue"),
        ],
        "bulk_revenue": category_summaries["bulk"],
        "sewing_revenue": category_summaries["sewing"],
        "sample_revenue": category_summaries["sample"],
        "sample_rows": sample_rows,
        "sample_invoice_count": len(sample_rows),
        "other_revenue": category_summaries["other"],
        "other_rows": other_rows,
        "unlinked_production_invoice_rows": unlinked_production_invoice_rows,
        "company_revenue": company_revenue,
        "export_rows": export_rows,
        "accounting_reconciliation": _accounting_reconciliation(
            all_accounting,
            company_revenue,
            client=client,
            brand=brand,
            country=country,
        ),
        "unclassified_sample_invoices": unclassified_sample_invoices,
        "unclassified_revenue": unclassified_revenue,
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
