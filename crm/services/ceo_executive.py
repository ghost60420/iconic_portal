from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from crm.models import AccountingEntry, Invoice, ProductionOrder, Shipment
from crm.services.ceo_approval_queue import count_ceo_approval_queue_items
from crm.services.employee_identity import get_employee_identity_index, resolve_employee_identity
from crm.services.sales_attribution import build_ceo_sales_kpis


CURRENCIES = ("CAD", "USD", "BDT")
ACTIVE_PRODUCTION_STATUSES = {
    "planning",
    "pattern",
    "sample_development",
    "sample_sent",
    "approved",
    "fabric_sourcing",
    "cutting",
    "sewing",
    "printing",
    "finishing",
    "qc",
    "packing",
    "ready_to_ship",
    "on_hold",
}
PROFIT_EXPENSE_TYPES = {"COGS", "EXPENSE", "TAX", "OTHER"}


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _currency_rows(amounts=None):
    amounts = amounts or {}
    return [{"currency": currency, "amount": _decimal(amounts.get(currency))} for currency in CURRENCIES]


def _sum_by_currency(queryset, field_name):
    return {
        row["currency"]: _decimal(row["amount"])
        for row in queryset.values("currency").annotate(amount=Sum(field_name))
        if row.get("currency") in CURRENCIES
    }


def _invoice_balance_by_currency(queryset):
    balance = ExpressionWrapper(
        F("total_amount") - F("paid_amount"),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )
    return {
        row["currency"]: _decimal(row["amount"])
        for row in queryset.values("currency").annotate(amount=Sum(balance))
        if row.get("currency") in CURRENCIES
    }


def _cash_by_currency():
    amounts = defaultdict(Decimal)
    rows = (
        AccountingEntry.objects.exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .values("currency", "direction")
        .annotate(amount=Sum("amount_original"))
    )
    for row in rows:
        currency = (row.get("currency") or "").upper()
        if currency not in CURRENCIES:
            continue
        amount = _decimal(row["amount"])
        amounts[currency] += amount if row["direction"] == AccountingEntry.DIR_IN else -amount
    return amounts


def _revenue_profit_by_currency(month_start, today):
    revenue = defaultdict(Decimal)
    expenses = defaultdict(Decimal)
    rows = (
        AccountingEntry.objects.filter(date__range=(month_start, today))
        .exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .values("currency", "direction", "main_type")
        .annotate(amount=Sum("amount_original"))
    )
    for row in rows:
        currency = (row.get("currency") or "").upper()
        if currency not in CURRENCIES:
            continue
        amount = _decimal(row["amount"])
        main_type = (row.get("main_type") or "").upper()
        if row["direction"] == AccountingEntry.DIR_IN and main_type == "INCOME":
            revenue[currency] += amount
        elif row["direction"] == AccountingEntry.DIR_OUT and main_type in PROFIT_EXPENSE_TYPES:
            expenses[currency] += amount
    profit = {currency: revenue[currency] - expenses[currency] for currency in CURRENCIES}
    return revenue, profit


def build_ceo_executive_context():
    today = timezone.localdate()
    month_start = today.replace(day=1)
    live_invoices = Invoice.objects.filter(is_archived=False).exclude(status="cancelled")

    sales_kpis = build_ceo_sales_kpis(today)
    outstanding_ar = _invoice_balance_by_currency(
        live_invoices.filter(total_amount__gt=F("paid_amount"))
    )
    outstanding_ap = _sum_by_currency(
        AccountingEntry.objects.filter(direction=AccountingEntry.DIR_OUT)
        .exclude(main_type="TRANSFER")
        .exclude(
            Q(status__iexact="PAID")
            | Q(status__iexact="CANCELLED")
            | Q(status__iexact="VOID")
        ),
        "amount_original",
    )
    current_cash = _cash_by_currency()
    revenue, profit = _revenue_profit_by_currency(month_start, today)

    production_groups = list(
        ProductionOrder.objects.filter(is_archived=False)
        .values("assigned_production_manager_id")
        .annotate(
            total=Count("id"),
            active=Count("id", filter=Q(operational_status__in=ACTIVE_PRODUCTION_STATUSES)),
            late=Count(
                "id",
                filter=Q(bulk_deadline__lt=today)
                & ~Q(operational_status__in=["shipped", "cancelled"]),
            ),
        )
    )
    production = {
        key: sum(int(row[key] or 0) for row in production_groups)
        for key in ("total", "active", "late")
    }
    pending_approvals = count_ceo_approval_queue_items()

    top_production_managers = [
        {
            "assigned_production_manager_id": row["assigned_production_manager_id"],
            "count": int(row["total"] or 0),
        }
        for row in sorted(
            (row for row in production_groups if row["assigned_production_manager_id"]),
            key=lambda row: (-int(row["total"] or 0), row["assigned_production_manager_id"]),
        )[:5]
    ]
    identity_index = get_employee_identity_index()
    for row in top_production_managers:
        row["label"] = resolve_employee_identity(
            user_id=row["assigned_production_manager_id"],
            index=identity_index,
        )["canonical_name"]

    upcoming_shipments = list(
        Shipment.objects.filter(ship_date__gte=today)
        .exclude(status__in=["delivered", "cancelled"])
        .select_related("order", "customer")
        .order_by("ship_date", "id")[:8]
    )

    return {
        "today": today,
        "month_start": month_start,
        "today_sales": sales_kpis["today_sales"],
        "monthly_sales": sales_kpis["monthly_sales"],
        "outstanding_ar": _currency_rows(outstanding_ar),
        "outstanding_ap": _currency_rows(outstanding_ap),
        "current_cash": _currency_rows(current_cash),
        "revenue_by_currency": _currency_rows(revenue),
        "profit_by_currency": _currency_rows(profit),
        "open_pipeline_count": sales_kpis["open_pipeline_count"],
        "open_pipeline_rows": sales_kpis["open_pipeline_rows"],
        "production_total": int(production["total"] or 0),
        "production_active": int(production["active"] or 0),
        "late_production_orders": int(production["late"] or 0),
        "pending_ceo_approvals": pending_approvals,
        "top_customers": sales_kpis["top_customers"],
        "top_salespeople": sales_kpis["top_salespeople"],
        "top_production_managers": top_production_managers,
        "upcoming_shipments": upcoming_shipments,
    }
