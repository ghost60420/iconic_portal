from collections import defaultdict
from decimal import Decimal
from datetime import date, timedelta

from django.db.models import Count, DecimalField, Exists, ExpressionWrapper, F, OuterRef, Q, Sum
from django.utils import timezone

from crm.models import ExchangeRate, Invoice, Opportunity, ProductionOrder
from crm.services.costing_currency import (
    CurrencyConversionError,
    convert_currency,
    currency_summary_rows,
    format_finance_money,
)
from crm.services.pipeline import open_pipeline_queryset, summarize_pipeline, with_pipeline_value
from crm.services.production_operational_status import (
    OPERATIONAL_ACTIVE_STATUSES,
    OPERATIONAL_FINISHED_STATUSES,
    OPERATIONAL_STATUS_LABELS,
    get_production_operational_status,
)


INVOICE_EXCLUDED_STATUSES = ("paid", "cancelled")
PRODUCTION_EXCLUDED_LEGACY_STATUSES = ("done", "closed_won", "closed_lost", "cancelled")
PRODUCTION_ALERT_WINDOW_DAYS = 7


def decimal_or_zero(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def format_currency_rows(rows):
    return " / ".join(row["display"] for row in rows if row.get("amount") is not None) or "-"


def _add_display(rows):
    for row in rows:
        currency = (row.get("currency") or "CAD").upper()
        row["currency"] = currency
        row["amount"] = decimal_or_zero(row.get("amount"))
        row["display"] = format_finance_money(row["amount"], currency)
    return rows


def latest_cad_to_bdt_rate():
    try:
        row = ExchangeRate.objects.order_by("-updated_at").first()
    except Exception:
        return Decimal("0")
    return decimal_or_zero(getattr(row, "cad_to_bdt", None))


def active_sales_opportunity_queryset(queryset=None):
    queryset = queryset if queryset is not None else Opportunity.objects.all()
    production_subquery = ProductionOrder.objects.filter(
        opportunity_id=OuterRef("pk"),
        is_archived=False,
    )
    return (
        open_pipeline_queryset(queryset)
        .annotate(briefing_has_production=Exists(production_subquery))
        .filter(briefing_has_production=False)
        .exclude(stage="Production")
    )


def apply_opportunity_side_filter(queryset, side):
    side = (side or "").upper()
    if side not in {"CA", "BD"}:
        return queryset
    return queryset.filter(Q(lead__market=side) | Q(customer__market=side)).distinct()


def build_open_opportunity_metrics(*, side="", date_to=None, limit=10):
    date_to = date_to or timezone.localdate()
    queryset = active_sales_opportunity_queryset(
        Opportunity.objects.select_related("customer", "lead")
    )
    queryset = apply_opportunity_side_filter(queryset, side)
    valued_queryset = with_pipeline_value(queryset)
    summary = summarize_pipeline(queryset, apply_open_definition=False)
    summary_rows = _add_display(summary["rows"])
    due_queryset = valued_queryset.filter(Q(next_followup__lte=date_to) | Q(next_followup__isnull=True))
    zero_value_count = valued_queryset.filter(Q(pipeline_value__isnull=True) | Q(pipeline_value__lte=0)).count()
    rows = list(
        valued_queryset
        .order_by("next_followup", "-pipeline_value", "-updated_at", "-id")[:limit]
    )
    for opportunity in rows:
        amount = decimal_or_zero(getattr(opportunity, "pipeline_value", None))
        currency = (getattr(opportunity, "pipeline_currency", None) or "CAD").upper()
        opportunity.briefing_pipeline_value = amount
        opportunity.briefing_pipeline_currency = currency
        opportunity.briefing_pipeline_display = format_finance_money(amount, currency)

    return {
        "count": summary["count"],
        "pipeline_rows": summary_rows,
        "pipeline_display": format_currency_rows(summary_rows),
        "rows": rows,
        "visible_count": len(rows),
        "due_followup_count": due_queryset.count(),
        "zero_value_count": zero_value_count,
        "stage_counts": list(queryset.values("stage").annotate(count=Count("id")).order_by("stage")),
    }


def invoice_balance_expression():
    return ExpressionWrapper(
        F("total_amount") - F("paid_amount"),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )


def open_invoice_balance_queryset(queryset=None):
    queryset = queryset if queryset is not None else Invoice.objects.all()
    return (
        queryset.filter(is_archived=False)
        .exclude(status__in=INVOICE_EXCLUDED_STATUSES)
        .annotate(briefing_balance=invoice_balance_expression())
        .filter(briefing_balance__gt=0)
    )


def apply_invoice_side_filter(queryset, side):
    side = (side or "").upper()
    if side not in {"CA", "BD"}:
        return queryset
    default_currency = "BDT" if side == "BD" else "CAD"
    return queryset.filter(Q(invoice_region=side) | Q(invoice_region="", currency=default_currency))


def invoice_balance_totals_by_currency(queryset):
    rows = currency_summary_rows(
        {
            (row.get("currency") or "CAD").upper(): {"amount": row.get("amount")}
            for row in queryset.values("currency").annotate(amount=Sum("briefing_balance"))
        }
    )
    return _add_display(rows)


def _customer_label(customer):
    if not customer:
        return "No customer"
    return getattr(customer, "account_brand", "") or getattr(customer, "contact_name", "") or str(customer)


def _invoice_side(invoice):
    if getattr(invoice, "invoice_region", ""):
        return invoice.invoice_region
    if getattr(invoice, "currency", "") == "BDT":
        return "BD"
    return "CA"


def _cad_equivalent_for_invoice_balance(invoice, bdt_per_cad):
    amount = decimal_or_zero(getattr(invoice, "briefing_balance", None))
    currency = (getattr(invoice, "currency", "") or "CAD").upper()
    if currency == "CAD":
        return amount
    if currency == "BDT" and bdt_per_cad > 0:
        try:
            return convert_currency(amount, "BDT", "CAD", bdt_per_cad=bdt_per_cad)
        except CurrencyConversionError:
            return Decimal("0")
    return Decimal("0")


def build_receivable_metrics(*, side="", today=None):
    today = today or timezone.localdate()
    queryset = apply_invoice_side_filter(open_invoice_balance_queryset(), side)
    outstanding_rows = invoice_balance_totals_by_currency(queryset)
    overdue_queryset = queryset.filter(due_date__lt=today)
    overdue_rows_by_currency = invoice_balance_totals_by_currency(overdue_queryset)
    bdt_per_cad = latest_cad_to_bdt_rate()
    overdue_invoice_rows = []
    overdue_cad_equivalent = Decimal("0")
    for invoice in (
        overdue_queryset
        .select_related("customer", "order", "order__customer")
        .order_by("due_date", "-issue_date", "id")
    ):
        balance = decimal_or_zero(getattr(invoice, "briefing_balance", None))
        currency = (invoice.currency or "CAD").upper()
        overdue_cad_equivalent += _cad_equivalent_for_invoice_balance(invoice, bdt_per_cad)
        overdue_invoice_rows.append(
            {
                "invoice": invoice,
                "customer": _customer_label(invoice.customer or getattr(invoice.order, "customer", None)),
                "balance": balance,
                "currency": currency,
                "balance_display": format_finance_money(balance, currency),
                "due_date": invoice.due_date,
                "side": _invoice_side(invoice),
            }
        )

    return {
        "outstanding_count": queryset.count(),
        "outstanding_rows": outstanding_rows,
        "outstanding_display": format_currency_rows(outstanding_rows),
        "overdue_count": len(overdue_invoice_rows),
        "overdue_rows": overdue_rows_by_currency,
        "overdue_display": format_currency_rows(overdue_rows_by_currency),
        "overdue_invoice_rows": overdue_invoice_rows,
        "overdue_cad_equivalent": overdue_cad_equivalent,
        "summary_matches_rows": len(overdue_invoice_rows) == overdue_queryset.count(),
        "excluded_statuses": INVOICE_EXCLUDED_STATUSES,
    }


def build_production_alert_metrics(*, side="", today=None, alert_window_days=PRODUCTION_ALERT_WINDOW_DAYS):
    today = today or timezone.localdate()
    queryset = ProductionOrder.objects.filter(is_archived=False).exclude(
        status__in=PRODUCTION_EXCLUDED_LEGACY_STATUSES
    )
    if side == "CA":
        queryset = queryset.filter(factory_location="ca")
    elif side == "BD":
        queryset = queryset.filter(factory_location="bd")

    rows = []
    for order in (
        queryset.select_related("customer", "opportunity", "product")
        .prefetch_related("stages", "shipments")
        .only(
            "order_code",
            "title",
            "status",
            "operational_status",
            "bulk_deadline",
            "qty_total",
            "factory_location",
            "production_order_type",
            "style_name",
            "notes",
            "accessories_note",
            "extra_order_note",
            "fabric_required_kg",
            "fabric_received_kg",
            "updated_at",
            "is_archived",
            "customer__account_brand",
            "customer__contact_name",
            "opportunity__opportunity_id",
            "product__name",
        )
    ):
        operational_status = get_production_operational_status(order)
        if (
            operational_status not in OPERATIONAL_ACTIVE_STATUSES
            or operational_status in OPERATIONAL_FINISHED_STATUSES
        ):
            continue
        order.briefing_operational_status = operational_status
        order.briefing_operational_status_label = OPERATIONAL_STATUS_LABELS.get(
            operational_status,
            order.get_status_display(),
        )
        order.briefing_is_delayed = bool(order.bulk_deadline and order.bulk_deadline < today)
        rows.append({"order": order, "operational_status": operational_status})

    alert_until = today + timedelta(days=alert_window_days)
    delayed_rows = [
        row for row in rows
        if row["order"].bulk_deadline and row["order"].bulk_deadline < today
    ]
    due_soon_rows = [
        row for row in rows
        if row["order"].bulk_deadline and today <= row["order"].bulk_deadline <= alert_until
    ]
    alert_rows = [
        row for row in rows
        if row["order"].bulk_deadline and row["order"].bulk_deadline <= alert_until
    ]
    alert_rows.sort(key=lambda row: (row["order"].bulk_deadline or date.max, row["order"].updated_at, row["order"].pk))

    return {
        "active_count": len(rows),
        "delayed_count": len(delayed_rows),
        "due_soon_count": len(due_soon_rows),
        "alert_rows": [row["order"] for row in alert_rows],
        "finished_statuses": OPERATIONAL_FINISHED_STATUSES,
        "active_statuses": OPERATIONAL_ACTIVE_STATUSES,
    }
