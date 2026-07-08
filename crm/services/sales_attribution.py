"""Single source of truth for lead-derived sales attribution and KPI values.

Salesperson attribution always follows the related Lead.  Creator/author fields
are intentionally separate and never affect commercial attribution.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.urls import reverse
from django.db import models
from django.core.cache import cache
from django.db.models import Count, DecimalField, Exists, ExpressionWrapper, F, Max, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date

from crm.models import (
    CostingHeader,
    CRMAuditLog,
    Customer,
    EmployeeProfile,
    Invoice,
    InvoicePayment,
    Lead,
    LeadActivity,
    Opportunity,
    ProductionOrder,
    QuickCosting,
    Shipment,
)
from crm.services.employee_identity import (
    build_employee_identity_index,
    canonical_employee_name,
    employee_lead_ownership_q,
    get_employee_identity_index,
    known_employee_owner_q,
    resolve_employee_identity,
)
from crm.services.pipeline import CLOSED_PIPELINE_STAGES, NON_OPEN_PIPELINE_STAGES, summarize_pipeline, with_pipeline_value
from crm.services.production_operational_status import (
    OPERATIONAL_ACTIVE_STATUSES,
    OPERATIONAL_STATUS_CANCELLED,
    OPERATIONAL_STATUS_READY_TO_SHIP,
    OPERATIONAL_STATUS_SHIPPED,
)


CURRENCIES = ("CAD", "USD", "BDT")
ZERO = Decimal("0")
CHART_COLORS = {
    "CAD": "#d6b45a",
    "USD": "#9fb7ff",
    "BDT": "#34d399",
}
ISSUED_INVOICE_STATUSES = ("sent", "partial", "paid")
OPEN_INVOICE_STATUSES = ("draft", "sent", "partial")
ACTIVE_PRODUCTION_STATUSES = ("planning", "in_progress", "hold")
LEAD_CONVERTED_OUTBOUND_STATUSES = {"Converted to Opportunity"}
LEAD_CLOSED_OUTBOUND_STATUSES = {"Archived", "Bad Fit"}
LEAD_CLOSED_STATUSES = {"Lost", "Unqualified"}
LEAD_TERMINAL_STATUSES = {"Converted", *LEAD_CLOSED_STATUSES}


def _empty_rows():
    return {currency: {"amount": ZERO, "count": 0} for currency in CURRENCIES}


def _rows(grouped, *, amount_key="amount"):
    values = _empty_rows()
    for row in grouped:
        currency = (row.get("currency") or "").upper()
        if currency in values:
            values[currency]["amount"] += row.get(amount_key) or ZERO
            values[currency]["count"] += int(row.get("count") or 0)
    return [{"currency": currency, **values[currency]} for currency in CURRENCIES]


def _currency_rows(amounts, counts=None):
    counts = counts or {}
    return [
        {
            "currency": currency,
            "amount": amounts.get(currency, ZERO),
            "count": int(counts.get(currency, 0) or 0),
        }
        for currency in CURRENCIES
    ]


def _add_currency_amount(amounts, counts, currency, amount, *, count=1):
    currency = (currency or "").upper()
    if currency not in CURRENCIES:
        return
    amounts[currency] += amount or ZERO
    counts[currency] += int(count or 0)


def _shift_month(month_start, offset):
    month = month_start.month - 1 + offset
    year = month_start.year + month // 12
    month = month % 12 + 1
    return month_start.replace(year=year, month=month, day=1)


def _last_12_months(today):
    first_month = _shift_month(today.replace(day=1), -11)
    return [_shift_month(first_month, offset) for offset in range(12)]


def _month_end(month_start):
    return _shift_month(month_start, 1) - timedelta(days=1)


def _month_key(value):
    return value.replace(day=1).isoformat()


def _chart_url(name, **params):
    clean = {
        key: value
        for key, value in params.items()
        if value not in (None, "", [])
    }
    query = urlencode(clean)
    return f"{reverse(name)}?{query}" if query else reverse(name)


def _decimal_to_float(value):
    return float(value or ZERO)


def _invoice_product_type(invoice):
    opportunity = getattr(invoice, "opportunity", None)
    if opportunity:
        return _record_label(opportunity.product_type, opportunity.product_category, fallback="Other")
    order = getattr(invoice, "order", None)
    if order:
        order_opportunity = getattr(order, "opportunity", None)
        order_lead = getattr(order, "lead", None)
        product = getattr(order, "product", None)
        return _record_label(
            getattr(order, "product_type_snapshot", ""),
            getattr(order_opportunity, "product_type", ""),
            getattr(order_opportunity, "product_category", ""),
            getattr(order_lead, "primary_product_type", ""),
            getattr(product, "name", ""),
            fallback="Other",
        )
    costing = getattr(invoice, "costing_header", None)
    if costing and getattr(costing, "opportunity", None):
        return _record_label(costing.opportunity.product_type, costing.opportunity.product_category, fallback="Other")
    quick = getattr(invoice, "quick_costing", None)
    if quick and getattr(quick, "opportunity", None):
        return _record_label(quick.opportunity.product_type, quick.opportunity.product_category, fallback="Other")
    return "Other"


def _build_sales_chart_data(user, *, today, lead_counts, opportunity_counts, production_counts, opportunity_rows, invoices, production_orders):
    months = _last_12_months(today)
    month_keys = [_month_key(month) for month in months]
    monthly_revenue = {
        currency: {key: {"amount": ZERO, "count": 0} for key in month_keys}
        for currency in CURRENCIES
    }
    product_revenue = defaultdict(lambda: {
        "amounts": {currency: ZERO for currency in CURRENCIES},
        "counts": {currency: 0 for currency in CURRENCIES},
    })

    for invoice in invoices:
        currency = (invoice.currency or "").upper()
        if currency not in CURRENCIES or invoice.status not in ISSUED_INVOICE_STATUSES:
            continue
        issue_date = invoice.issue_date
        if issue_date:
            key = _month_key(issue_date)
            if key in monthly_revenue[currency]:
                monthly_revenue[currency][key]["amount"] += invoice.total_amount or ZERO
                monthly_revenue[currency][key]["count"] += 1
        product_type = _invoice_product_type(invoice)
        product_revenue[product_type]["amounts"][currency] += invoice.total_amount or ZERO
        product_revenue[product_type]["counts"][currency] += 1

    chart_width = 360
    chart_height = 130
    left = 30
    top = 14
    plot_width = 306
    plot_height = 82
    max_revenue = max(
        [_decimal_to_float(monthly_revenue[currency][key]["amount"]) for currency in CURRENCIES for key in month_keys]
        or [0]
    )
    revenue_series = []
    for currency in CURRENCIES:
        points = []
        point_rows = []
        for index, month in enumerate(months):
            key = _month_key(month)
            amount = monthly_revenue[currency][key]["amount"]
            x = left + (plot_width / 11 * index if len(months) > 1 else 0)
            y = top + plot_height - ((_decimal_to_float(amount) / max_revenue) * plot_height if max_revenue else 0)
            points.append(f"{x:.1f},{y:.1f}")
            point_rows.append({
                "x": f"{x:.1f}",
                "y": f"{y:.1f}",
                "amount": amount,
                "count": monthly_revenue[currency][key]["count"],
                "label": month.strftime("%b %Y"),
                "url": _chart_url(
                    "invoice_list",
                    date_from=month.isoformat(),
                    date_to=_month_end(month).isoformat(),
                    currency=currency,
                    salesperson=user.pk,
                ),
            })
        revenue_series.append({
            "currency": currency,
            "color": CHART_COLORS[currency],
            "points": " ".join(points),
            "points_meta": point_rows,
        })

    pipeline_items = [
        {
            "key": "active_leads",
            "label": "Active Leads",
            "count": lead_counts["active"],
            "url": _chart_url("leads_list", status="active", assigned_to=user.pk),
            "color": "#d6b45a",
        },
        {
            "key": "opportunities",
            "label": "Opportunities",
            "count": opportunity_counts["active"],
            "url": _chart_url("opportunities_list", status="active", salesperson=user.pk),
            "color": "#9fb7ff",
        },
        {
            "key": "production",
            "label": "Production",
            "count": production_counts["active"],
            "url": _chart_url("production_list", status="active", salesperson=user.pk),
            "color": "#34d399",
        },
        {
            "key": "ready_to_ship",
            "label": "Ready to Ship",
            "count": production_counts["ready_to_ship"],
            "url": _chart_url("production_list", status="ready_to_ship", salesperson=user.pk),
            "color": "#f59e0b",
        },
        {
            "key": "shipped",
            "label": "Shipped",
            "count": production_counts["shipped"],
            "url": _chart_url("production_list", status="shipped", salesperson=user.pk),
            "color": "#e7d28f",
        },
    ]
    pipeline_total = sum(item["count"] for item in pipeline_items)
    circumference = 263.89
    offset = 0
    for item in pipeline_items:
        length = (item["count"] / pipeline_total * circumference) if pipeline_total else 0
        item["dash"] = f"{length:.2f} {circumference - length:.2f}"
        item["offset"] = f"{-offset:.2f}"
        item["percent"] = round((item["count"] / pipeline_total * 100), 1) if pipeline_total else 0
        offset += length

    monthly_order_counts = {key: 0 for key in month_keys}
    for opportunity in opportunity_rows:
        created_date = opportunity.get("created_date")
        if created_date:
            key = _month_key(created_date)
            if key in monthly_order_counts:
                monthly_order_counts[key] += 1
    for order in production_orders:
        created_at = getattr(order, "created_at", None)
        if created_at:
            key = _month_key(created_at.date())
            if key in monthly_order_counts:
                monthly_order_counts[key] += 1
    max_orders = max(monthly_order_counts.values() or [0])
    monthly_orders = []
    bar_width = 18
    for index, month in enumerate(months):
        key = _month_key(month)
        count = monthly_order_counts[key]
        height = (count / max_orders * plot_height) if max_orders else 0
        x = left + (plot_width / 12 * index) + 4
        y = top + plot_height - height
        monthly_orders.append({
            "label": month.strftime("%b"),
            "full_label": month.strftime("%b %Y"),
            "count": count,
            "x": f"{x:.1f}",
            "y": f"{y:.1f}",
            "width": bar_width,
            "height": f"{height:.1f}",
            "url": _chart_url(
                "opportunities_list",
                status="all",
                created_from=month.isoformat(),
                created_to=_month_end(month).isoformat(),
                salesperson=user.pk,
            ),
        })

    product_max_by_currency = {
        currency: max([values["amounts"][currency] for values in product_revenue.values()] or [ZERO])
        for currency in CURRENCIES
    }
    product_rows = []
    for product_type, values in product_revenue.items():
        total_count = sum(values["counts"].values())
        bars = []
        for currency in CURRENCIES:
            amount = values["amounts"][currency]
            max_amount = product_max_by_currency[currency]
            bars.append({
                "currency": currency,
                "amount": amount,
                "count": values["counts"][currency],
                "width": int((amount / max_amount * 100)) if max_amount else 0,
                "color": CHART_COLORS[currency],
            })
        product_rows.append({
            "label": product_type or "Other",
            "count": total_count,
            "bars": bars,
            "url": _chart_url("opportunities_list", status="all", q=product_type, salesperson=user.pk),
        })
    product_rows.sort(key=lambda row: (-row["count"], row["label"]))

    return {
        "monthly_revenue": {
            "width": chart_width,
            "height": chart_height,
            "months": [
                {
                    "label": month.strftime("%b"),
                    "x": f"{left + (plot_width / 11 * index if len(months) > 1 else 0):.1f}",
                }
                for index, month in enumerate(months)
            ],
            "series": revenue_series,
            "has_data": any(
                monthly_revenue[currency][key]["amount"] for currency in CURRENCIES for key in month_keys
            ),
        },
        "pipeline_distribution": {
            "items": pipeline_items,
            "total": pipeline_total,
            "has_data": bool(pipeline_total),
        },
        "monthly_orders": {
            "width": chart_width,
            "height": chart_height,
            "months": monthly_orders,
            "has_data": bool(max_orders),
        },
        "revenue_by_product_type": {
            "rows": product_rows[:8],
            "has_data": bool(product_rows),
        },
    }


def _lead_has_opportunity_annotation():
    return Exists(Opportunity.objects.filter(lead_id=OuterRef("pk")))


def _opportunity_has_production_annotation():
    return Exists(ProductionOrder.objects.filter(opportunity_id=OuterRef("pk"), is_archived=False))


def _is_active_lead_record(lead):
    return bool(
        not getattr(lead, "is_archived", False)
        and getattr(lead, "lead_status", "") not in LEAD_TERMINAL_STATUSES
        and getattr(lead, "outbound_status", "") not in (LEAD_CONVERTED_OUTBOUND_STATUSES | LEAD_CLOSED_OUTBOUND_STATUSES)
        and not getattr(lead, "sales_has_opportunity", False)
    )


def _is_converted_lead_record(lead):
    return bool(
        getattr(lead, "lead_status", "") == "Converted"
        or getattr(lead, "outbound_status", "") in LEAD_CONVERTED_OUTBOUND_STATUSES
        or getattr(lead, "sales_has_opportunity", False)
    )


def _is_active_opportunity_row(opportunity):
    return bool(
        opportunity.get("is_open")
        and opportunity.get("stage") not in NON_OPEN_PIPELINE_STAGES
        and not opportunity.get("sales_has_production")
    )


def _is_moved_to_production_opportunity_row(opportunity):
    return bool(opportunity.get("stage") == "Production" or opportunity.get("sales_has_production"))


def _production_currency_and_value(order):
    if order.order_type == "sewing_charge" and order.factory_location == "bd":
        return "BDT", (order.qty_total or 0) * (order.sewing_charge_per_piece_bdt or ZERO)
    return (order.approved_currency or "CAD").upper(), order.approved_total_value or ZERO


def _production_status_bucket(order):
    operational_status = order.operational_status or ""
    delivered = bool(getattr(order, "sales_has_delivered_shipment", False))
    completed = bool(order.status == "done" or (operational_status == OPERATIONAL_STATUS_SHIPPED and delivered))
    if completed:
        return "completed"
    if operational_status == OPERATIONAL_STATUS_SHIPPED:
        return "shipped"
    if operational_status == OPERATIONAL_STATUS_READY_TO_SHIP:
        return "ready_to_ship"
    if operational_status == OPERATIONAL_STATUS_CANCELLED:
        return "cancelled"
    if operational_status in OPERATIONAL_ACTIVE_STATUSES:
        return "active"
    if order.status in {"planning", "in_progress", "hold"}:
        return "active"
    return "active"


def _invoice_paid_amount_from_history(invoice):
    payments = list(invoice.payments.all())
    if payments:
        return sum((payment.amount or ZERO) for payment in payments)
    return invoice.paid_amount or ZERO


def _invoice_balance_from_history(invoice):
    balance = (invoice.total_amount or ZERO) - _invoice_paid_amount_from_history(invoice)
    return balance if balance > ZERO else ZERO


def _record_label(*values, fallback=""):
    for value in values:
        if value:
            return value
    return fallback


def lead_ownership_q(user, prefix=""):
    return employee_lead_ownership_q(user, prefix=prefix)


def production_ownership_q(user, prefix=""):
    """Prefer an explicit order lead; otherwise inherit the opportunity lead."""
    return (
        lead_ownership_q(user, f"{prefix}lead__")
        | (
            Q(**{f"{prefix}lead__isnull": True})
            & lead_ownership_q(user, f"{prefix}opportunity__lead__")
        )
    )


def shipment_ownership_q(user, prefix=""):
    """Resolve shipment ownership through production first, then opportunity."""
    order = Q(**{f"{prefix}order__isnull": False}) & production_ownership_q(user, f"{prefix}order__")
    no_order = Q(**{f"{prefix}order__isnull": True})
    opportunity = no_order & lead_ownership_q(user, f"{prefix}opportunity__lead__")
    return order | opportunity


def invoice_ownership_q(user, prefix=""):
    """Resolve one invoice owner using deterministic relationship precedence."""
    order = Q(**{f"{prefix}order__isnull": False}) & production_ownership_q(user, f"{prefix}order__")
    no_order = Q(**{f"{prefix}order__isnull": True})
    direct_opportunity = (
        no_order
        & Q(**{f"{prefix}opportunity__isnull": False})
        & lead_ownership_q(user, f"{prefix}opportunity__lead__")
    )
    advanced = (
        no_order
        & Q(**{f"{prefix}opportunity__isnull": True})
        & Q(**{f"{prefix}costing_header__isnull": False})
        & lead_ownership_q(user, f"{prefix}costing_header__opportunity__lead__")
    )
    quick = (
        no_order
        & Q(**{f"{prefix}opportunity__isnull": True})
        & Q(**{f"{prefix}costing_header__isnull": True})
        & lead_ownership_q(user, f"{prefix}quick_costing__opportunity__lead__")
    )
    return order | direct_opportunity | advanced | quick


def _lead_for_record(record):
    if isinstance(record, Lead):
        return record
    if isinstance(record, Opportunity):
        return record.lead
    if isinstance(record, (QuickCosting, CostingHeader)):
        return record.opportunity.lead if record.opportunity_id else None
    if isinstance(record, ProductionOrder):
        if record.lead_id:
            return record.lead
        return record.opportunity.lead if record.opportunity_id else None
    if isinstance(record, Shipment):
        if record.order_id:
            return _lead_for_record(record.order)
        return record.opportunity.lead if record.opportunity_id else None
    if isinstance(record, Invoice):
        if record.order_id:
            return _lead_for_record(record.order)
        if record.opportunity_id:
            return _lead_for_record(record.opportunity)
        if record.costing_header_id:
            return _lead_for_record(record.costing_header)
        if record.quick_costing_id:
            return _lead_for_record(record.quick_costing)
        return None
    if isinstance(record, InvoicePayment):
        return _lead_for_record(record.invoice)
    if isinstance(record, Customer):
        leads = record.leads.filter(is_archived=False).select_related("assigned_to").order_by("-created_date", "-id")
        attributed = leads.filter(Q(assigned_to__isnull=False) | ~Q(owner="")).first()
        return attributed or leads.first()
    order = getattr(record, "order", None)
    opportunity = getattr(record, "opportunity", None)
    return _lead_for_record(order or opportunity) if (order or opportunity) else None


def resolve_salesperson_for_record(record, *, index=None):
    """Canonical salesperson-of-record helper for dashboard/report consumers."""
    return attribution_for(record, index=index, include_author=False)["salesperson"]


def _author_user_id(record):
    for field_name in ("created_by", "quoted_by"):
        user_id = getattr(record, f"{field_name}_id", None)
        if user_id is not None:
            return user_id
    return None


def _audit_author(record):
    module_by_model = {
        "Customer": "customers",
        "Lead": "leads",
        "Opportunity": "opportunities",
        "CostingHeader": "quotations",
        "QuickCosting": "quick_costing",
        "ProductionOrder": "production",
        "Invoice": "invoices",
        "Shipment": "shipments",
    }
    module = module_by_model.get(record.__class__.__name__)
    if not module or not getattr(record, "pk", None):
        return None
    audit = (
        CRMAuditLog.objects.filter(
            module=module,
            record_id=str(record.pk),
            action_type=CRMAuditLog.ACTION_CREATED,
            actor__isnull=False,
        )
        .select_related("actor", "actor__employee_profile")
        .order_by("created_at", "id")
        .first()
    )
    return audit.actor if audit else None


def _cached_identity(user, *, index=None):
    if user is None:
        return None
    if index is not None:
        return resolve_employee_identity(user_id=user.pk, assigned_user=user, index=index)
    profile = user._state.fields_cache.get("employee_profile")
    if profile is None:
        return None
    canonical_name = canonical_employee_name(
        profile_display_name=profile.display_name,
        profile_full_name=profile.full_name,
        user_full_name=user.get_full_name(),
        username=user.get_username(),
    )
    cache.set(f"crm-employee-display:{user.pk}", profile.display_name or "", 300)
    return {
        "profile_id": profile.pk,
        "user_id": user.pk,
        "employee_id": profile.employee_id or "",
        "canonical_name": canonical_name,
        "display_name": profile.display_name or "",
        "full_name": user.get_full_name() or "",
        "username": user.get_username(),
        "aliases": profile.aliases or [],
    }


def attribution_for(record, *, index=None, include_author=True):
    """Return separately labelled salesperson-of-record and record author."""
    lead = _lead_for_record(record)
    assigned_user = lead._state.fields_cache.get("assigned_to") if lead else None
    salesperson = _cached_identity(assigned_user, index=index)
    if salesperson is None and lead:
        lookup_index = index or get_employee_identity_index()
        salesperson = resolve_employee_identity(
            user_id=lead.assigned_to_id, owner_text=lead.owner, index=lookup_index
        )
    if salesperson is None:
        salesperson = resolve_employee_identity(index=index or {"by_user_id": {}, "by_profile_id": {}, "by_token": {}})
    author_id = _author_user_id(record) if include_author else None
    author_user = None
    for field_name in (("created_by", "quoted_by") if include_author else ()):
        if getattr(record, f"{field_name}_id", None):
            author_user = record._state.fields_cache.get(field_name)
            break
    if include_author and author_user is None and not author_id:
        author_user = _audit_author(record)
        author_id = author_user.pk if author_user else None
    author_identity = _cached_identity(author_user, index=index)
    if author_identity is None and author_id:
        lookup_index = index or get_employee_identity_index()
        author_identity = resolve_employee_identity(user_id=author_id, index=lookup_index)
    if author_identity is None:
        author_identity = {
            "profile_id": None,
            "user_id": None,
            "employee_id": "",
            "canonical_name": "Unavailable",
            "display_name": "Unavailable",
            "full_name": "",
            "username": "",
            "aliases": [],
        }
    return {
        "salesperson": salesperson,
        "author": author_identity,
        "lead_id": getattr(lead, "lead_id", "") if lead else "",
    }


def _quoted_values(user):
    quick_value = ExpressionWrapper(
        F("quantity") * F("selling_price_per_piece"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    quick = (
        QuickCosting.objects.filter(lead_ownership_q(user, "opportunity__lead__"), quotation_number__gt="")
        .exclude(status=QuickCosting.STATUS_REJECTED)
        .values("currency")
        .annotate(
            amount=Sum(quick_value),
            count=Count("id"),
            approved=Count("id", filter=Q(status=QuickCosting.STATUS_APPROVED)),
        )
    )
    advanced_unit = Coalesce("manual_fob_per_piece", "opportunity__costing_fob_per_piece", ZERO)
    advanced_value = ExpressionWrapper(
        advanced_unit * F("order_quantity"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    advanced = (
        CostingHeader.objects.filter(
            lead_ownership_q(user, "opportunity__lead__"),
            is_archived=False,
            quotation_number__gt="",
        )
        .exclude(quotation_status__in=(CostingHeader.QUOTATION_STATUS_REJECTED, CostingHeader.QUOTATION_STATUS_DECLINED))
        .values("currency")
        .annotate(
            amount=Sum(advanced_value),
            count=Count("id"),
            approved=Count("id", filter=Q(quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED)),
        )
    )
    quick = list(quick)
    advanced = list(advanced)
    quick_rows = _rows(quick)
    advanced_rows = _rows(advanced)
    combined = []
    for quick_row, advanced_row in zip(quick_rows, advanced_rows):
        combined.append({
            "currency": quick_row["currency"],
            "amount": quick_row["amount"] + advanced_row["amount"],
            "count": quick_row["count"] + advanced_row["count"],
        })
    approved_count = sum(int(row.get("approved") or 0) for row in quick + advanced)
    return quick_rows, advanced_rows, combined, approved_count


def _production_values(user):
    local_value = ExpressionWrapper(
        F("qty_total") * F("sewing_charge_per_piece_bdt"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    queryset = ProductionOrder.objects.filter(
        production_ownership_q(user),
        is_archived=False,
        status__in=ACTIVE_PRODUCTION_STATUSES,
    ).annotate(
        currency=Coalesce(
            models.Case(
                models.When(order_type="sewing_charge", factory_location="bd", then=models.Value("BDT")),
                default=F("approved_currency"),
                output_field=models.CharField(max_length=10),
            ),
            models.Value("CAD"),
        ),
        value=Coalesce(
            models.Case(
                models.When(order_type="sewing_charge", factory_location="bd", then=local_value),
                default=F("approved_total_value"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
            ZERO,
        ),
    )
    grouped = list(
        queryset.values("currency").annotate(
            amount=Sum("value"),
            count=Count("id"),
            available_count=Count(
                "id",
                filter=Q(approved_total_value__isnull=False)
                | Q(order_type="sewing_charge", factory_location="bd", sewing_charge_per_piece_bdt__isnull=False),
            ),
        )
    )
    rows = _rows(grouped)
    available = {(row.get("currency") or "").upper(): int(row.get("available_count") or 0) for row in grouped}
    for row in rows:
        row["available_count"] = available.get(row["currency"], 0)
        row["unavailable_count"] = row["count"] - row["available_count"]
    return rows


def build_sales_kpis(user):
    """Build the canonical KPI set in at most ten bounded database queries."""
    today = timezone.localdate()
    month_start = today.replace(day=1)
    leads = (
        Lead.objects.filter(lead_ownership_q(user))
        .annotate(sales_has_opportunity=_lead_has_opportunity_annotation())
    )
    lead_rows = list(
        leads.select_related("customer").only(
            "id", "lead_id", "account_brand", "contact_name", "lead_status", "outbound_status",
            "created_date", "next_followup", "next_follow_up_date", "customer_id", "is_archived",
            "customer__is_active", "customer__is_archived",
        ).annotate(
            activity_follow_ups=Count("activities", filter=Q(activities__activity_type="follow_up_sent")),
            activity_calls=Count("activities", filter=Q(activities__activity_type="call_made")),
            activity_emails=Count("activities", filter=Q(activities__activity_type="cold_email_sent")),
            activity_meetings=Count("activities", filter=Q(activities__activity_type="meeting_booked")),
            activity_conversions=Count("activities", filter=Q(activities__activity_type="converted")),
        )
    )
    active_lead_rows = [lead for lead in lead_rows if _is_active_lead_record(lead)]
    converted_lead_rows = [lead for lead in lead_rows if not lead.is_archived and _is_converted_lead_record(lead)]
    closed_lead_rows = [
        lead for lead in lead_rows
        if not lead.is_archived
        and not _is_converted_lead_record(lead)
        and (
            lead.lead_status in LEAD_CLOSED_STATUSES
            or lead.outbound_status in LEAD_CLOSED_OUTBOUND_STATUSES
        )
    ]
    lead_counts = {
        "total": len(lead_rows),
        "open": len(active_lead_rows),
        "active": len(active_lead_rows),
        "converted": len(converted_lead_rows),
        "lost": len(closed_lead_rows),
        "due_today": sum(lead.next_followup == today or lead.next_follow_up_date == today for lead in lead_rows),
        "overdue": sum(
            bool(
                (lead.next_followup and lead.next_followup < today)
                or (lead.next_follow_up_date and lead.next_follow_up_date < today)
            )
            for lead in lead_rows
        ),
    }

    opportunities = (
        Opportunity.objects.filter(is_archived=False)
        .filter(lead_ownership_q(user, "lead__"))
        .annotate(sales_has_production=_opportunity_has_production_annotation())
    )
    opportunity_rows = list(
        with_pipeline_value(opportunities).values(
            "id", "stage", "is_open", "created_date", "updated_at", "closed_won_at",
            "customer_id", "opportunity_id", "product_type", "product_category", "lead__account_brand",
            "sales_has_production", "pipeline_currency", "pipeline_value",
        )
    )
    active_opportunity_rows = [row for row in opportunity_rows if _is_active_opportunity_row(row)]
    moved_opportunity_rows = [row for row in opportunity_rows if _is_moved_to_production_opportunity_row(row)]
    won_totals = _empty_rows()
    monthly_won_totals = _empty_rows()
    lost_month_totals = _empty_rows()
    pipeline_totals = defaultdict(Decimal)
    pipeline_counts = defaultdict(int)
    total_order_totals = defaultdict(Decimal)
    total_order_counts = defaultdict(int)
    opportunity_counts = {
        "open": len(active_opportunity_rows),
        "active": len(active_opportunity_rows),
        "moved_to_production": len(moved_opportunity_rows),
        "won": 0,
        "lost": 0,
    }
    sales_cycles = []
    won_customer_ids = set()
    for opportunity in opportunity_rows:
        currency = (opportunity["pipeline_currency"] or "CAD").upper()
        value = opportunity["pipeline_value"] or ZERO
        if currency in CURRENCIES and opportunity["stage"] not in {"Closed Lost", "Cancelled"}:
            total_order_totals[currency] += value
            total_order_counts[currency] += 1
        if _is_active_opportunity_row(opportunity) and currency in CURRENCIES:
            pipeline_totals[currency] += value
            pipeline_counts[currency] += 1
        if opportunity["stage"] == "Closed Won":
            opportunity_counts["won"] += 1
            won_customer_ids.add(opportunity["customer_id"])
            if currency in won_totals:
                won_totals[currency]["amount"] += value
                won_totals[currency]["count"] += 1
            closed_at = opportunity["closed_won_at"]
            won_date = closed_at.date() if closed_at else opportunity["created_date"]
            if month_start <= won_date <= today:
                monthly_won_totals[currency]["amount"] += value
                monthly_won_totals[currency]["count"] += 1
            if closed_at:
                sales_cycles.append(max((closed_at.date() - opportunity["created_date"]).days, 0))
        elif opportunity["stage"] == "Closed Lost":
            opportunity_counts["lost"] += 1
            if month_start <= opportunity["updated_at"].date() <= today and currency in lost_month_totals:
                lost_month_totals[currency]["amount"] += value
                lost_month_totals[currency]["count"] += 1

    closed_won_rows = [{"currency": currency, **won_totals[currency]} for currency in CURRENCIES]
    monthly_won_rows = [{"currency": currency, **monthly_won_totals[currency]} for currency in CURRENCIES]
    lost_month_rows = [{"currency": currency, **lost_month_totals[currency]} for currency in CURRENCIES]
    pipeline_rows = _currency_rows(pipeline_totals, pipeline_counts)
    total_order_rows = _currency_rows(total_order_totals, total_order_counts)
    quick_quotes, advanced_quotes, combined_quotes, approved_quote_count = _quoted_values(user)

    invoices = Invoice.objects.filter(
        invoice_ownership_q(user), is_archived=False
    ).exclude(status="cancelled").distinct().select_related(
        "customer",
        "opportunity",
        "order",
        "order__product",
        "order__lead",
        "order__opportunity",
        "order__opportunity__lead",
        "costing_header",
        "costing_header__opportunity",
        "quick_costing",
        "quick_costing__opportunity",
    ).prefetch_related("payments", "sales_commissions")
    invoice_totals = _empty_rows()
    payment_totals = _empty_rows()
    commission_totals = _empty_rows()
    commission_eligible_totals = _empty_rows()
    outstanding_totals = defaultdict(Decimal)
    outstanding_counts = defaultdict(int)
    invoice_customer_counts = defaultdict(int)
    open_invoice_count = 0
    paid_invoice_count = 0
    recent_invoices = []
    for invoice in invoices:
        currency = (invoice.currency or "").upper()
        payment_total = _invoice_paid_amount_from_history(invoice)
        outstanding = _invoice_balance_from_history(invoice)
        if invoice.status in ISSUED_INVOICE_STATUSES and currency in invoice_totals:
            invoice_totals[currency]["amount"] += invoice.total_amount or ZERO
            invoice_totals[currency]["count"] += 1
        if invoice.customer_id:
            invoice_customer_counts[invoice.customer_id] += 1
        for payment in invoice.payments.all():
            payment_currency = (payment.currency or "").upper()
            if payment_currency in payment_totals:
                payment_totals[payment_currency]["amount"] += payment.amount or ZERO
                payment_totals[payment_currency]["count"] += 1
        if invoice.status in OPEN_INVOICE_STATUSES and outstanding > ZERO:
            open_invoice_count += 1
            _add_currency_amount(outstanding_totals, outstanding_counts, currency, outstanding)
        if invoice.status == "paid" or (invoice.status != "draft" and payment_total >= (invoice.total_amount or ZERO) and invoice.total_amount):
            paid_invoice_count += 1
        for commission in invoice.sales_commissions.all():
            commission_currency = (commission.currency or "").upper()
            if commission_currency in commission_totals:
                commission_totals[commission_currency]["amount"] += commission.commission_amount or ZERO
                commission_totals[commission_currency]["count"] += 1
                commission_eligible_totals[commission_currency]["amount"] += commission.eligible_amount or ZERO
                commission_eligible_totals[commission_currency]["count"] += 1
        recent_invoices.append(
            {
                "id": invoice.pk,
                "invoice_number": invoice.invoice_number,
                "customer": _record_label(
                    getattr(invoice.customer, "account_brand", ""),
                    getattr(invoice.customer, "contact_name", ""),
                    fallback="No customer",
                ),
                "status": invoice.get_status_display(),
                "currency": currency,
                "total": invoice.total_amount or ZERO,
                "paid": payment_total,
                "balance": outstanding,
                "issue_date": invoice.issue_date,
            }
        )
    invoice_rows = [{"currency": currency, **invoice_totals[currency]} for currency in CURRENCIES]
    payment_rows = [{"currency": currency, **payment_totals[currency]} for currency in CURRENCIES]
    outstanding_rows = _currency_rows(outstanding_totals, outstanding_counts)

    production_orders = list(
        ProductionOrder.objects.filter(production_ownership_q(user), is_archived=False)
        .annotate(
            sales_has_delivered_shipment=Exists(
                Shipment.objects.filter(order_id=OuterRef("pk"), status="delivered")
            )
        )
        .select_related("customer", "lead", "opportunity", "product")
        .only(
            "id", "order_code", "title", "status", "operational_status", "order_type",
            "factory_location", "qty_total", "sewing_charge_per_piece_bdt", "approved_currency",
            "approved_total_value", "created_at", "bulk_deadline", "customer__account_brand",
            "customer__contact_name", "lead__account_brand", "opportunity__opportunity_id",
            "product__name",
        )
        .order_by("-created_at", "-id")
    )
    production_value_totals = defaultdict(Decimal)
    production_value_counts = defaultdict(int)
    production_table_rows = []
    ready_to_ship_rows = []
    production_counts = {
        "total": 0,
        "active": 0,
        "ready_to_ship": 0,
        "shipped": 0,
        "completed": 0,
        "cancelled": 0,
        "month": 0,
    }
    for order in production_orders:
        bucket = _production_status_bucket(order)
        production_counts["total"] += 1
        production_counts[bucket] = production_counts.get(bucket, 0) + 1
        currency, value = _production_currency_and_value(order)
        if bucket in {"active", "ready_to_ship"}:
            _add_currency_amount(production_value_totals, production_value_counts, currency, value)
        row = {
            "id": order.pk,
            "purchase_order_number": order.purchase_order_number,
            "title": order.title,
            "customer": _record_label(
                getattr(order.customer, "account_brand", ""),
                getattr(order.customer, "contact_name", ""),
                getattr(order.lead, "account_brand", ""),
                fallback="No customer",
            ),
            "status": order.get_operational_status_display(),
            "bucket": bucket,
            "currency": currency,
            "value": value,
            "quantity": order.qty_total,
            "created_at": order.created_at,
        }
        if bucket == "ready_to_ship":
            ready_to_ship_rows.append(row)
        if bucket == "active":
            production_table_rows.append(row)
    production_rows = _currency_rows(production_value_totals, production_value_counts)

    active_customer_ids = {
        lead.customer_id for lead in lead_rows
        if lead.customer_id and lead.customer and lead.customer.is_active and not lead.customer.is_archived
    }
    customer_counts = {
        "active": len(active_customer_ids),
        "won": len(won_customer_ids - {None}),
        "repeat": sum(count >= 2 for count in invoice_customer_counts.values()),
    }
    activity_counts = {
        "leads": lead_counts["total"],
        "follow_ups": sum(lead.activity_follow_ups for lead in lead_rows),
        "calls": sum(lead.activity_calls for lead in lead_rows),
        "emails": sum(lead.activity_emails for lead in lead_rows),
        "meetings": sum(lead.activity_meetings for lead in lead_rows),
        "conversions": sum(lead.activity_conversions for lead in lead_rows),
    }
    completed = opportunity_counts["won"] + opportunity_counts["lost"]
    closing_ratio = (
        (Decimal(opportunity_counts["won"]) / Decimal(completed) * Decimal("100")).quantize(Decimal("0.01"))
        if completed else ZERO
    )
    average_cycle = (
        (Decimal(sum(sales_cycles)) / Decimal(len(sales_cycles))).quantize(Decimal("0.1"))
        if sales_cycles else ZERO
    )
    commission_rows = [{"currency": currency, **commission_totals[currency]} for currency in CURRENCIES]
    commission_eligible_rows = [{"currency": currency, **commission_eligible_totals[currency]} for currency in CURRENCIES]
    sales_charts = _build_sales_chart_data(
        user,
        today=today,
        lead_counts=lead_counts,
        opportunity_counts=opportunity_counts,
        production_counts=production_counts,
        opportunity_rows=opportunity_rows,
        invoices=invoices,
        production_orders=production_orders,
    )
    metrics = {
        "lead_counts": lead_counts,
        "opportunity_counts": opportunity_counts,
        "pipeline_value": pipeline_rows,
        "pipeline_count": sum(row["count"] for row in pipeline_rows),
        "closed_won_value": closed_won_rows,
        "closed_won_count": opportunity_counts["won"],
        "monthly_closed_won_value": monthly_won_rows,
        "lost_this_month": lost_month_rows,
        "won_this_month_count": sum(row["count"] for row in monthly_won_rows),
        "lost_this_month_count": sum(row["count"] for row in lost_month_rows),
        "closing_ratio": closing_ratio,
        "average_sales_cycle_days": average_cycle,
        "quick_quoted_value": quick_quotes,
        "advanced_quoted_value": advanced_quotes,
        "quoted_value": combined_quotes,
        "invoice_values": invoice_rows,
        "collected_values": payment_rows,
        "outstanding_balance": outstanding_rows,
        "total_order_value": total_order_rows,
        "total_invoice_value": invoice_rows,
        "production_values": production_rows,
        "customer_counts": customer_counts,
        "activity_counts": activity_counts,
        "commission_values": commission_rows,
        "commission_eligible_values": commission_eligible_rows,
        "sales_charts": sales_charts,
        "production_counts": production_counts,
        "invoice_counts": {
            "open": open_invoice_count,
            "paid": paid_invoice_count,
            "total": sum(row["count"] for row in invoice_rows),
        },
        "owner_counts": {
            "active_leads": lead_counts["active"],
            "converted_leads": lead_counts["converted"],
            "active_opportunities": opportunity_counts["active"],
            "opportunities_moved_to_production": opportunity_counts["moved_to_production"],
            "active_production_orders": production_counts["active"],
            "ready_to_ship_orders": production_counts["ready_to_ship"],
            "shipped_orders": production_counts["shipped"],
            "completed_orders": production_counts["completed"],
            "open_invoices": open_invoice_count,
            "paid_invoices": paid_invoice_count,
        },
        "owner_tables": {
            "active_leads": [
                {
                    "id": lead.pk,
                    "lead_id": lead.lead_id,
                    "customer": _record_label(lead.account_brand, lead.contact_name, fallback="No customer"),
                    "status": lead.lead_status or "New",
                    "date": lead.created_date,
                }
                for lead in sorted(active_lead_rows, key=lambda item: (item.created_date, item.pk), reverse=True)[:10]
            ],
            "active_opportunities": [
                {
                    "id": row["id"],
                    "opportunity_id": row["opportunity_id"],
                    "customer": row["lead__account_brand"] or "No customer",
                    "stage": row["stage"],
                    "currency": (row["pipeline_currency"] or "CAD").upper(),
                    "value": row["pipeline_value"] or ZERO,
                }
                for row in sorted(active_opportunity_rows, key=lambda item: (item["created_date"], item["id"]), reverse=True)[:10]
            ],
            "production_orders": production_table_rows[:10],
            "ready_to_ship_orders": ready_to_ship_rows[:10],
            "recent_invoices": sorted(recent_invoices, key=lambda item: (item["issue_date"], item["id"]), reverse=True)[:10],
        },
        "quotation_counts": {
            "open": sum(row["count"] for row in combined_quotes),
            "approved": approved_quote_count,
        },
    }
    # Compatibility names are defined here so every dashboard consumes the
    # same values without recomputing them in a view or adapter.
    metrics.update(
        {
            "sales_revenue": metrics["closed_won_value"],
            "monthly_sales_revenue": metrics["monthly_closed_won_value"],
            "average_deal_value": metrics["closed_won_value"],
            "paid_invoice_values": metrics["collected_values"],
            "paid_invoice_count": paid_invoice_count,
        }
    )
    return metrics


def build_employee_sales_statistics(user):
    """Canonical compact employee statistics derived from the KPI service."""
    metrics = build_sales_kpis(user)
    last_activity = LeadActivity.objects.filter(
        Q(user=user) | Q(lead__assigned_to=user)
    ).aggregate(last=Max("created_at"))["last"]
    return {
        "leads": metrics["lead_counts"]["total"],
        "open_opportunities": metrics["opportunity_counts"]["open"],
        "won_opportunities": metrics["opportunity_counts"]["won"],
        "production_orders": metrics["production_counts"]["total"],
        "invoices": sum(row["count"] for row in metrics["invoice_values"]),
        "revenue": metrics["sales_revenue"],
        "closing_ratio": metrics["closing_ratio"],
        "average_deal_size": metrics["average_deal_value"],
        "last_activity": last_activity,
    }


def _team_filter_value(filters, key):
    if not filters:
        return ""
    try:
        return (filters.get(key) or "").strip()
    except AttributeError:
        return (filters.get(key, "") or "").strip()


def _team_filters(filters=None):
    date_from = parse_date(_team_filter_value(filters, "date_from"))
    date_to = parse_date(_team_filter_value(filters, "date_to"))
    salesperson = _team_filter_value(filters, "salesperson")
    try:
        salesperson_id = int(salesperson) if salesperson else None
    except ValueError:
        salesperson_id = None
    return {
        "date_from": date_from,
        "date_to": date_to,
        "salesperson_id": salesperson_id,
        "market": _team_filter_value(filters, "market"),
        "status": _team_filter_value(filters, "status"),
        "product_type": _team_filter_value(filters, "product_type"),
    }


def _identity_for_owner_columns(row, *, index, direct_prefix="", fallback_prefix=""):
    direct_user = row.get(f"{direct_prefix}assigned_to_id")
    direct_owner = row.get(f"{direct_prefix}owner")
    if direct_user or direct_owner:
        return resolve_employee_identity(user_id=direct_user, owner_text=direct_owner, index=index)
    return resolve_employee_identity(
        user_id=row.get(f"{fallback_prefix}assigned_to_id"),
        owner_text=row.get(f"{fallback_prefix}owner"),
        index=index,
    )


def _row_allowed(row, selected_user_id):
    return not selected_user_id or row.get("user_id") == selected_user_id


def _apply_common_date_filter(queryset, filters, field_name):
    if filters["date_from"]:
        queryset = queryset.filter(**{f"{field_name}__gte": filters["date_from"]})
    if filters["date_to"]:
        queryset = queryset.filter(**{f"{field_name}__lte": filters["date_to"]})
    return queryset


def build_team_sales_kpis(filters=None):
    """Canonical, bounded-query aggregation for Team Performance."""
    filters = _team_filters(filters)
    sales_profiles = list(
        EmployeeProfile.objects.filter(user__groups__name="Sales", is_archived=False)
        .select_related("user", "manager", "manager__employee_profile")
        .distinct()
        .order_by("display_name", "user__username")
    )
    user_ids = [profile.user_id for profile in sales_profiles]
    profile_by_user = {profile.user_id: profile for profile in sales_profiles}
    identity_index = build_employee_identity_index(sales_profiles)
    rows = {
        user_id: {
            "profile": profile,
            "name": profile.public_name,
            "opportunities": 0,
            "won": 0,
            "lost": 0,
            "closing_ratio": ZERO,
            "overdue_followups": 0,
            "completed_followups": 0,
            "revenue": {currency: ZERO for currency in CURRENCIES},
            "revenue_count": {currency: 0 for currency in CURRENCIES},
            "invoice_revenue": {currency: ZERO for currency in CURRENCIES},
            "invoice_revenue_count": {currency: 0 for currency in CURRENCIES},
            "outstanding": {currency: ZERO for currency in CURRENCIES},
            "leads": 0,
            "converted_leads": 0,
            "production": 0,
            "ready_to_ship": 0,
            "shipped": 0,
        }
        for user_id, profile in profile_by_user.items()
    }
    if user_ids:
        known_sales_owner = known_employee_owner_q(index=identity_index)
        lead_scope = Q(assigned_to_id__in=user_ids)
        if known_sales_owner:
            lead_scope |= Q(assigned_to__isnull=True) & known_sales_owner
        for row in (
            Lead.objects.filter(is_archived=False)
            .annotate(sales_has_opportunity=_lead_has_opportunity_annotation())
            .filter(lead_scope)
            .filter(
                Q(market=filters["market"]) if filters["market"] else Q(),
                Q(lead_status__iexact=filters["status"]) | Q(outbound_status__iexact=filters["status"]) if filters["status"] else Q(),
                Q(primary_product_type__icontains=filters["product_type"]) | Q(product_category__icontains=filters["product_type"]) if filters["product_type"] else Q(),
            )
            .filter(
                **({"created_date__gte": filters["date_from"]} if filters["date_from"] else {}),
            )
            .filter(
                **({"created_date__lte": filters["date_to"]} if filters["date_to"] else {}),
            )
            .values("assigned_to_id", "owner")
            .annotate(
                active=Count(
                    "id",
                    filter=(
                        ~Q(lead_status__in=LEAD_TERMINAL_STATUSES)
                        & ~Q(outbound_status__in=LEAD_CONVERTED_OUTBOUND_STATUSES | LEAD_CLOSED_OUTBOUND_STATUSES)
                        & Q(sales_has_opportunity=False)
                    ),
                    distinct=True,
                ),
                converted=Count(
                    "id",
                    filter=Q(lead_status="Converted")
                    | Q(outbound_status__in=LEAD_CONVERTED_OUTBOUND_STATUSES)
                    | Q(sales_has_opportunity=True),
                    distinct=True,
                ),
                overdue=Count(
                    "id",
                    filter=Q(next_followup__lt=timezone.localdate())
                    | Q(next_follow_up_date__lt=timezone.localdate()),
                    distinct=True,
                ),
                completed_followups=Count(
                    "activities",
                    filter=Q(activities__activity_type="follow_up_sent"),
                    distinct=True,
                ),
            )
        ):
            identity = resolve_employee_identity(
                user_id=row["assigned_to_id"], owner_text=row["owner"], index=identity_index
            )
            if identity["user_id"] in rows and _row_allowed(identity, filters["salesperson_id"]):
                rows[identity["user_id"]]["leads"] += int(row["active"] or 0)
                rows[identity["user_id"]]["converted_leads"] += int(row["converted"] or 0)
                rows[identity["user_id"]]["overdue_followups"] += int(row["overdue"] or 0)
                rows[identity["user_id"]]["completed_followups"] += int(row["completed_followups"] or 0)

        opportunity_scope = Q(lead__assigned_to_id__in=user_ids)
        known_opportunity_owner = known_employee_owner_q(prefix="lead__", index=identity_index)
        if known_opportunity_owner:
            opportunity_scope |= Q(lead__assigned_to__isnull=True) & known_opportunity_owner
        opportunity_qs = Opportunity.objects.filter(is_archived=False).filter(opportunity_scope)
        opportunity_qs = _apply_common_date_filter(opportunity_qs, filters, "created_date")
        if filters["market"]:
            opportunity_qs = opportunity_qs.filter(lead__market=filters["market"])
        if filters["status"]:
            opportunity_qs = opportunity_qs.filter(stage__iexact=filters["status"])
        if filters["product_type"]:
            opportunity_qs = opportunity_qs.filter(
                Q(product_type__icontains=filters["product_type"])
                | Q(product_category__icontains=filters["product_type"])
                | Q(lead__primary_product_type__icontains=filters["product_type"])
            )
        opportunity_rows = list(
            with_pipeline_value(opportunity_qs.annotate(sales_has_production=_opportunity_has_production_annotation()))
            .values("lead__assigned_to_id", "lead__owner", "pipeline_currency")
            .annotate(
                total=Count("id"),
                active=Count(
                    "id",
                    filter=Q(is_open=True)
                    & ~Q(stage__in=NON_OPEN_PIPELINE_STAGES)
                    & Q(sales_has_production=False),
                ),
                won=Count("id", filter=Q(stage="Closed Won")),
                lost=Count("id", filter=Q(stage="Closed Lost")),
                revenue=Sum("pipeline_value", filter=Q(stage="Closed Won")),
            )
        )
        for row in opportunity_rows:
            identity = resolve_employee_identity(
                user_id=row["lead__assigned_to_id"], owner_text=row["lead__owner"], index=identity_index
            )
            if identity["user_id"] not in rows:
                continue
            if not _row_allowed(identity, filters["salesperson_id"]):
                continue
            item = rows[identity["user_id"]]
            item["opportunities"] += int(row["active"] or 0)
            item["won"] += int(row["won"] or 0)
            item["lost"] += int(row["lost"] or 0)
            currency = (row["pipeline_currency"] or "").upper()
            if currency in CURRENCIES:
                # Closed-won opportunity value is retained for leader cards.
                item["revenue"][currency] += row["revenue"] or ZERO

        known_production_lead_owner = known_employee_owner_q(prefix="lead__", index=identity_index)
        known_production_opportunity_owner = known_employee_owner_q(prefix="opportunity__lead__", index=identity_index)
        production_scope = Q(lead__assigned_to_id__in=user_ids)
        if known_production_lead_owner:
            production_scope |= Q(lead__assigned_to__isnull=True) & known_production_lead_owner
        opportunity_production_scope = Q(opportunity__lead__assigned_to_id__in=user_ids)
        if known_production_opportunity_owner:
            opportunity_production_scope |= (
                Q(opportunity__lead__assigned_to__isnull=True) & known_production_opportunity_owner
            )
        production_scope |= Q(lead__isnull=True) & opportunity_production_scope
        production_qs = ProductionOrder.objects.filter(is_archived=False).filter(production_scope)
        production_qs = production_qs.annotate(
            sales_has_delivered_shipment=Exists(
                Shipment.objects.filter(order_id=OuterRef("pk"), status="delivered")
            )
        )
        if filters["date_from"]:
            production_qs = production_qs.filter(created_at__date__gte=filters["date_from"])
        if filters["date_to"]:
            production_qs = production_qs.filter(created_at__date__lte=filters["date_to"])
        if filters["market"]:
            production_qs = production_qs.filter(
                Q(lead__market=filters["market"]) | Q(opportunity__lead__market=filters["market"])
            )
        if filters["status"]:
            production_qs = production_qs.filter(
                Q(operational_status__iexact=filters["status"]) | Q(status__iexact=filters["status"])
            )
        if filters["product_type"]:
            production_qs = production_qs.filter(
                Q(product_type_snapshot__icontains=filters["product_type"])
                | Q(title__icontains=filters["product_type"])
                | Q(opportunity__product_type__icontains=filters["product_type"])
                | Q(opportunity__product_category__icontains=filters["product_type"])
            )
        for row in production_qs.values(
            "lead__assigned_to_id",
            "lead__owner",
            "opportunity__lead__assigned_to_id",
            "opportunity__lead__owner",
            "operational_status",
            "status",
            "sales_has_delivered_shipment",
        ).annotate(total=Count("id")):
            identity = _identity_for_owner_columns(
                row,
                index=identity_index,
                direct_prefix="lead__",
                fallback_prefix="opportunity__lead__",
            )
            if identity["user_id"] not in rows or not _row_allowed(identity, filters["salesperson_id"]):
                continue
            pseudo_order = type("ProductionStatus", (), {
                "operational_status": row["operational_status"],
                "status": row["status"],
                "sales_has_delivered_shipment": row["sales_has_delivered_shipment"],
            })()
            bucket = _production_status_bucket(pseudo_order)
            count = int(row["total"] or 0)
            if bucket == "active":
                rows[identity["user_id"]]["production"] += count
            elif bucket == "ready_to_ship":
                rows[identity["user_id"]]["ready_to_ship"] += count
            elif bucket in {"shipped", "completed"}:
                rows[identity["user_id"]]["shipped"] += count

        order_lead_scope = Q(order__lead__assigned_to_id__in=user_ids)
        known_invoice_order_lead_owner = known_employee_owner_q(prefix="order__lead__", index=identity_index)
        if known_invoice_order_lead_owner:
            order_lead_scope |= Q(order__lead__assigned_to__isnull=True) & known_invoice_order_lead_owner
        order_opportunity_scope = Q(order__opportunity__lead__assigned_to_id__in=user_ids)
        known_invoice_order_opportunity_owner = known_employee_owner_q(prefix="order__opportunity__lead__", index=identity_index)
        if known_invoice_order_opportunity_owner:
            order_opportunity_scope |= (
                Q(order__opportunity__lead__assigned_to__isnull=True) & known_invoice_order_opportunity_owner
            )
        invoice_opportunity_scope = Q(opportunity__lead__assigned_to_id__in=user_ids)
        known_invoice_opportunity_owner = known_employee_owner_q(prefix="opportunity__lead__", index=identity_index)
        if known_invoice_opportunity_owner:
            invoice_opportunity_scope |= Q(opportunity__lead__assigned_to__isnull=True) & known_invoice_opportunity_owner
        costing_scope = Q(costing_header__opportunity__lead__assigned_to_id__in=user_ids)
        known_costing_owner = known_employee_owner_q(prefix="costing_header__opportunity__lead__", index=identity_index)
        if known_costing_owner:
            costing_scope |= Q(costing_header__opportunity__lead__assigned_to__isnull=True) & known_costing_owner
        quick_scope = Q(quick_costing__opportunity__lead__assigned_to_id__in=user_ids)
        known_quick_owner = known_employee_owner_q(prefix="quick_costing__opportunity__lead__", index=identity_index)
        if known_quick_owner:
            quick_scope |= Q(quick_costing__opportunity__lead__assigned_to__isnull=True) & known_quick_owner
        invoice_qs = Invoice.objects.filter(is_archived=False).exclude(status="cancelled").filter(
            Q(order__isnull=False) & (order_lead_scope | (Q(order__lead__isnull=True) & order_opportunity_scope))
            | Q(order__isnull=True, opportunity__isnull=False) & invoice_opportunity_scope
            | Q(order__isnull=True, opportunity__isnull=True, costing_header__isnull=False) & costing_scope
            | Q(order__isnull=True, opportunity__isnull=True, costing_header__isnull=True) & quick_scope
        )
        invoice_qs = _apply_common_date_filter(invoice_qs, filters, "issue_date")
        if filters["market"]:
            invoice_qs = invoice_qs.filter(
                Q(order__lead__market=filters["market"])
                | Q(order__opportunity__lead__market=filters["market"])
                | Q(opportunity__lead__market=filters["market"])
                | Q(invoice_region=filters["market"])
            )
        if filters["status"]:
            invoice_qs = invoice_qs.filter(status__iexact=filters["status"])
        if filters["product_type"]:
            invoice_qs = invoice_qs.filter(
                Q(order__product_type_snapshot__icontains=filters["product_type"])
                | Q(order__title__icontains=filters["product_type"])
                | Q(opportunity__product_type__icontains=filters["product_type"])
                | Q(opportunity__product_category__icontains=filters["product_type"])
            )
        for row in invoice_qs.values(
            "id",
            "currency",
            "status",
            "total_amount",
            "paid_amount",
            "order_id",
            "order__lead__assigned_to_id",
            "order__lead__owner",
            "order__opportunity__lead__assigned_to_id",
            "order__opportunity__lead__owner",
            "opportunity__lead__assigned_to_id",
            "opportunity__lead__owner",
            "costing_header__opportunity__lead__assigned_to_id",
            "costing_header__opportunity__lead__owner",
            "quick_costing__opportunity__lead__assigned_to_id",
            "quick_costing__opportunity__lead__owner",
        ).annotate(payment_total=Coalesce(Sum("payments__amount"), ZERO)):
            if row["order_id"]:
                identity = _identity_for_owner_columns(
                    row,
                    index=identity_index,
                    direct_prefix="order__lead__",
                    fallback_prefix="order__opportunity__lead__",
                )
            elif row["opportunity__lead__assigned_to_id"] or row["opportunity__lead__owner"]:
                identity = resolve_employee_identity(
                    user_id=row["opportunity__lead__assigned_to_id"],
                    owner_text=row["opportunity__lead__owner"],
                    index=identity_index,
                )
            elif row["costing_header__opportunity__lead__assigned_to_id"] or row["costing_header__opportunity__lead__owner"]:
                identity = resolve_employee_identity(
                    user_id=row["costing_header__opportunity__lead__assigned_to_id"],
                    owner_text=row["costing_header__opportunity__lead__owner"],
                    index=identity_index,
                )
            else:
                identity = resolve_employee_identity(
                    user_id=row["quick_costing__opportunity__lead__assigned_to_id"],
                    owner_text=row["quick_costing__opportunity__lead__owner"],
                    index=identity_index,
                )
            if identity["user_id"] not in rows or not _row_allowed(identity, filters["salesperson_id"]):
                continue
            currency = (row["currency"] or "").upper()
            if currency not in CURRENCIES:
                continue
            total = row["total_amount"] or ZERO
            paid = row["payment_total"] if row["payment_total"] not in (None, "") else (row["paid_amount"] or ZERO)
            balance = total - paid
            if row["status"] in ISSUED_INVOICE_STATUSES:
                rows[identity["user_id"]]["invoice_revenue"][currency] += total
                rows[identity["user_id"]]["invoice_revenue_count"][currency] += 1
            if row["status"] in OPEN_INVOICE_STATUSES and balance > ZERO:
                rows[identity["user_id"]]["outstanding"][currency] += balance

    team_rows = list(rows.values())
    for row in team_rows:
        completed = row["won"] + row["lost"]
        row["closing_ratio"] = (
            (Decimal(row["won"]) / Decimal(completed) * Decimal("100")).quantize(Decimal("0.01"))
            if completed else ZERO
        )
        row["revenue_rows"] = [
            {"currency": currency, "amount": row["invoice_revenue"][currency]} for currency in CURRENCIES
        ]
        row["closed_won_revenue_rows"] = [
            {"currency": currency, "amount": row["revenue"][currency]} for currency in CURRENCIES
        ]
        row["outstanding_rows"] = [
            {"currency": currency, "amount": row["outstanding"][currency]} for currency in CURRENCIES
        ]
    if filters["salesperson_id"]:
        team_rows = [row for row in team_rows if row["profile"].user_id == filters["salesperson_id"]]

    def leader(key):
        winner = max(team_rows, key=lambda item: (item[key], item["name"]), default=None)
        return winner if winner and winner[key] else None

    revenue_leaders = []
    for currency in CURRENCIES:
        winner = max(
            team_rows,
            key=lambda item: (item["revenue"][currency], item["name"]),
            default=None,
        )
        if winner and not winner["revenue"][currency]:
            winner = None
        revenue_leaders.append(
            {
                "currency": currency,
                "amount": winner["revenue"][currency] if winner else ZERO,
                "name": winner["name"] if winner else "No data",
            }
        )

    status_profiles = list(
        EmployeeProfile.objects.filter(
            is_archived=False,
            status__in=(EmployeeProfile.STATUS_ON_LEAVE, EmployeeProfile.STATUS_SUSPENDED),
        )
        .select_related("user")
        .order_by("display_name", "user__username")
    )
    newest_employees = sorted(sales_profiles, key=lambda profile: profile.user.date_joined, reverse=True)[:5]
    return {
        "team_rows": sorted(team_rows, key=lambda row: (-row["won"], row["name"])),
        "top_salesperson": leader("won"),
        "highest_closing_ratio": leader("closing_ratio"),
        "most_opportunities": leader("opportunities"),
        "most_followups_completed": leader("completed_followups"),
        "most_overdue_followups": leader("overdue_followups"),
        "revenue_leaders": revenue_leaders,
        "newest_employees": newest_employees,
        "employees_on_leave": [p for p in status_profiles if p.status == EmployeeProfile.STATUS_ON_LEAVE],
        "suspended_employees": [p for p in status_profiles if p.status == EmployeeProfile.STATUS_SUSPENDED],
        "sales_profiles": sales_profiles,
        "team_filters": filters,
    }


def _currency_amount_rows(amounts):
    return [{"currency": currency, "amount": amounts.get(currency, ZERO)} for currency in CURRENCIES]


def _rank_invoice_group(grouped, limit=5):
    ranked = sorted(grouped.items(), key=lambda item: (-item[1]["count"], item[0].lower()))[:limit]
    return [
        {
            "label": label,
            "count": values["count"],
            "amounts": _currency_amount_rows(values["amounts"]),
        }
        for label, values in ranked
    ]


def _ceo_invoice_kpis(queryset, *, today, month_start):
    """Aggregate all CEO invoice-sales widgets in one bounded invoice query."""
    today_amounts = defaultdict(Decimal)
    monthly_amounts = defaultdict(Decimal)
    customer_groups = defaultdict(lambda: {"count": 0, "amounts": defaultdict(Decimal)})
    salesperson_groups = defaultdict(lambda: {"count": 0, "amounts": defaultdict(Decimal)})
    identity_index = get_employee_identity_index()
    invoices = queryset.filter(issue_date__range=(month_start, today)).select_related(
        "customer",
        "order__lead__assigned_to",
        "order__opportunity__lead__assigned_to",
        "costing_header__opportunity__lead__assigned_to",
        "quick_costing__opportunity__lead__assigned_to",
    )
    for invoice in invoices:
        currency = (invoice.currency or "").upper()
        if currency not in CURRENCIES:
            continue
        amount = invoice.total_amount or ZERO
        monthly_amounts[currency] += amount
        if invoice.issue_date == today:
            today_amounts[currency] += amount

        customer = invoice.customer
        customer_label = (
            (customer.account_brand or customer.contact_name) if customer else None
        ) or "No customer"
        customer_groups[customer_label]["count"] += 1
        customer_groups[customer_label]["amounts"][currency] += amount

        if invoice.status in ISSUED_INVOICE_STATUSES:
            identity = attribution_for(invoice, index=identity_index, include_author=False)["salesperson"]
            if identity["user_id"] is not None or identity["canonical_name"] != "Unassigned":
                salesperson_label = identity["canonical_name"]
                salesperson_groups[salesperson_label]["count"] += 1
                salesperson_groups[salesperson_label]["amounts"][currency] += amount
    return {
        "today_sales": _currency_amount_rows(today_amounts),
        "monthly_sales": _currency_amount_rows(monthly_amounts),
        "top_customers": _rank_invoice_group(customer_groups),
        "top_salespeople": _rank_invoice_group(salesperson_groups),
    }


def build_ceo_sales_kpis(today=None):
    """All sales KPIs consumed by the CEO dashboard, from canonical sources."""
    today = today or timezone.localdate()
    month_start = today.replace(day=1)
    live_invoices = Invoice.objects.filter(is_archived=False).exclude(status="cancelled")
    invoice_kpis = _ceo_invoice_kpis(live_invoices, today=today, month_start=month_start)
    open_pipeline = summarize_pipeline(Opportunity.objects.all())
    return {
        **invoice_kpis,
        "open_pipeline_count": open_pipeline["count"],
        "open_pipeline_rows": open_pipeline["rows"],
    }
