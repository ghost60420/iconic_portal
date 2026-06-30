from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from crm.models import AccountingEntry, CostingHeader, Invoice, ProductionOrder, Shipment
from crm.services.employee_identity import get_employee_identity_index, resolve_employee_identity


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


def _ranked_invoice_people(queryset, dimensions, label_builder, limit=5):
    grouped = defaultdict(lambda: {"count": 0, "amounts": defaultdict(Decimal)})
    rows = queryset.values(*dimensions, "currency").annotate(
        amount=Sum("total_amount"),
        count=Count("id"),
    )
    for row in rows:
        label = label_builder(row)
        if not label:
            continue
        grouped[label]["count"] += int(row["count"] or 0)
        currency = (row.get("currency") or "").upper()
        if currency in CURRENCIES:
            grouped[label]["amounts"][currency] += _decimal(row["amount"])
    ranked = sorted(grouped.items(), key=lambda item: (-item[1]["count"], item[0].lower()))[:limit]
    return [
        {
            "label": label,
            "count": values["count"],
            "amounts": _currency_rows(values["amounts"]),
        }
        for label, values in ranked
    ]


def _ranked_invoice_salespeople(queryset, limit=5):
    grouped = defaultdict(lambda: {"count": 0, "amounts": defaultdict(Decimal)})
    identity_index = get_employee_identity_index()
    rows = queryset.values(
        "order__lead__assigned_to_id",
        "order__lead__owner",
        "costing_header__opportunity__lead__assigned_to_id",
        "costing_header__opportunity__lead__owner",
        "currency",
    ).annotate(amount=Sum("total_amount"), count=Count("id"))
    for row in rows:
        user_id = (
            row.get("order__lead__assigned_to_id")
            or row.get("costing_header__opportunity__lead__assigned_to_id")
        )
        owner_text = (
            row.get("order__lead__owner")
            or row.get("costing_header__opportunity__lead__owner")
        )
        identity = resolve_employee_identity(user_id=user_id, owner_text=owner_text, index=identity_index)
        if identity["user_id"] is None and not owner_text:
            continue
        label = identity["canonical_name"]
        grouped[label]["count"] += int(row["count"] or 0)
        currency = (row.get("currency") or "").upper()
        if currency in CURRENCIES:
            grouped[label]["amounts"][currency] += _decimal(row["amount"])
    ranked = sorted(grouped.items(), key=lambda item: (-item[1]["count"], item[0].lower()))[:limit]
    return [
        {
            "label": label,
            "count": values["count"],
            "amounts": _currency_rows(values["amounts"]),
        }
        for label, values in ranked
    ]


def build_ceo_executive_context():
    today = timezone.localdate()
    month_start = today.replace(day=1)
    live_invoices = Invoice.objects.exclude(status="cancelled")

    today_sales = _sum_by_currency(live_invoices.filter(issue_date=today), "total_amount")
    monthly_invoices = live_invoices.filter(issue_date__range=(month_start, today))
    monthly_sales = _sum_by_currency(monthly_invoices, "total_amount")
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

    production = ProductionOrder.objects.filter(is_archived=False).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(operational_status__in=ACTIVE_PRODUCTION_STATUSES)),
        late=Count(
            "id",
            filter=Q(bulk_deadline__lt=today)
            & ~Q(operational_status__in=["shipped", "cancelled"]),
        ),
    )
    pending_approvals = CostingHeader.objects.filter(
        status="approved",
        quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
    ).exclude(quotation_number="").count()

    top_customers = _ranked_invoice_people(
        monthly_invoices,
        ["customer__account_brand", "customer__contact_name"],
        lambda row: row.get("customer__account_brand") or row.get("customer__contact_name") or "No customer",
    )
    top_salespeople = _ranked_invoice_salespeople(monthly_invoices)
    top_production_managers = list(
        ProductionOrder.objects.filter(
            is_archived=False,
            assigned_production_manager__isnull=False,
        )
        .values("assigned_production_manager_id")
        .annotate(count=Count("id"))
        .order_by("-count", "assigned_production_manager_id")[:5]
    )
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
        "today_sales": _currency_rows(today_sales),
        "monthly_sales": _currency_rows(monthly_sales),
        "outstanding_ar": _currency_rows(outstanding_ar),
        "outstanding_ap": _currency_rows(outstanding_ap),
        "current_cash": _currency_rows(current_cash),
        "revenue_by_currency": _currency_rows(revenue),
        "profit_by_currency": _currency_rows(profit),
        "production_total": int(production["total"] or 0),
        "production_active": int(production["active"] or 0),
        "late_production_orders": int(production["late"] or 0),
        "pending_ceo_approvals": pending_approvals,
        "top_customers": top_customers,
        "top_salespeople": top_salespeople,
        "top_production_managers": top_production_managers,
        "upcoming_shipments": upcoming_shipments,
    }
