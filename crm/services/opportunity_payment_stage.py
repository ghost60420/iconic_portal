from collections import defaultdict
from decimal import Decimal

from django.db.models import DecimalField, Exists, ExpressionWrapper, F, OuterRef, Q

from crm.models import Invoice, Opportunity, ProductionOrder
from crm.services.costing_currency import currency_summary_rows, format_finance_money


AWAITING_PAYMENT_STAGE = "Awaiting Payment"


def decimal_or_zero(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def invoice_open_balance(invoice):
    return decimal_or_zero(getattr(invoice, "total_amount", None)) - decimal_or_zero(getattr(invoice, "paid_amount", None))


def invoice_has_outstanding_balance(invoice):
    if not invoice:
        return False
    if getattr(invoice, "is_archived", False):
        return False
    if (getattr(invoice, "status", "") or "").lower() == "cancelled":
        return False
    return invoice_open_balance(invoice) > 0


def opportunity_for_invoice(invoice):
    if not invoice:
        return None
    if getattr(invoice, "opportunity_id", None):
        return invoice.opportunity
    quick_costing = getattr(invoice, "quick_costing", None)
    if quick_costing and getattr(quick_costing, "opportunity_id", None):
        return quick_costing.opportunity
    costing = getattr(invoice, "costing_header", None)
    if costing and getattr(costing, "opportunity_id", None):
        return costing.opportunity
    order = getattr(invoice, "order", None)
    if order and getattr(order, "opportunity_id", None):
        return order.opportunity
    return None


def opportunity_has_active_production(opportunity):
    if not opportunity or not getattr(opportunity, "pk", None):
        return False
    return ProductionOrder.objects.filter(opportunity=opportunity, is_archived=False).exists()


def sync_opportunity_stage_from_invoice(invoice, *, save=True):
    """Move invoice-backed open opportunities with a balance into Awaiting Payment.

    Production orders remain the authority once a real production record exists.
    This avoids moving a valid production order back to a sales stage while still
    repairing orphan Production states with unpaid or partial invoices.
    """
    if not invoice or not getattr(invoice, "pk", None):
        return {"changed": False, "opportunity": None, "reason": "missing_invoice"}

    invoice = (
        Invoice.objects.select_related(
            "opportunity",
            "quick_costing__opportunity",
            "costing_header__opportunity",
            "order__opportunity",
        )
        .filter(pk=invoice.pk)
        .first()
    )
    opportunity = opportunity_for_invoice(invoice)
    if not opportunity:
        return {"changed": False, "opportunity": None, "reason": "missing_opportunity"}
    if getattr(opportunity, "is_archived", False):
        return {"changed": False, "opportunity": opportunity, "reason": "archived_opportunity"}
    if not invoice_has_outstanding_balance(invoice):
        return {"changed": False, "opportunity": opportunity, "reason": "no_outstanding_balance"}
    if opportunity_has_active_production(opportunity):
        return {"changed": False, "opportunity": opportunity, "reason": "production_exists"}
    if opportunity.stage == AWAITING_PAYMENT_STAGE:
        return {"changed": False, "opportunity": opportunity, "reason": "already_awaiting_payment"}

    opportunity.stage = AWAITING_PAYMENT_STAGE
    if save:
        opportunity.save(update_fields=["stage", "updated_at"])
    return {"changed": True, "opportunity": opportunity, "reason": "awaiting_payment"}


def _invoice_queryset_for_opportunity_ids(opportunity_ids):
    ids = list(opportunity_ids or [])
    if not ids:
        return Invoice.objects.none()
    return (
        Invoice.objects.select_related("opportunity", "quick_costing__opportunity", "costing_header__opportunity", "order__opportunity")
        .filter(
            Q(opportunity_id__in=ids)
            | Q(quick_costing__opportunity_id__in=ids)
            | Q(costing_header__opportunity_id__in=ids)
            | Q(order__opportunity_id__in=ids)
        )
        .distinct()
    )


def outstanding_balance_summary_for_opportunity(opportunity):
    if not opportunity or not getattr(opportunity, "pk", None):
        return {"rows": [], "display": "-", "invoice_count": 0}
    totals = defaultdict(lambda: {"amount": Decimal("0")})
    invoice_count = 0
    for invoice in _invoice_queryset_for_opportunity_ids([opportunity.pk]):
        if not invoice_has_outstanding_balance(invoice):
            continue
        linked = opportunity_for_invoice(invoice)
        if not linked or linked.pk != opportunity.pk:
            continue
        currency = (getattr(invoice, "currency", "") or "CAD").upper()
        totals[currency]["amount"] += invoice_open_balance(invoice)
        invoice_count += 1
    rows = currency_summary_rows(totals)
    for row in rows:
        row["display"] = format_finance_money(row["amount"], row["currency"])
    display = " / ".join(row["display"] for row in rows) or "-"
    return {"rows": rows, "display": display, "invoice_count": invoice_count}


def opportunity_has_outstanding_invoice(opportunity):
    return bool(outstanding_balance_summary_for_opportunity(opportunity)["rows"])


def awaiting_payment_queryset(queryset=None):
    queryset = queryset if queryset is not None else Opportunity.objects.all()
    production_exists = ProductionOrder.objects.filter(opportunity_id=OuterRef("pk"), is_archived=False)
    return (
        queryset.filter(stage=AWAITING_PAYMENT_STAGE, is_archived=False)
        .annotate(has_active_production_order=Exists(production_exists))
        .filter(has_active_production_order=False)
    )


def build_awaiting_payment_metrics(*, side=""):
    balance_expression = ExpressionWrapper(
        F("total_amount") - F("paid_amount"),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )
    invoice_queryset = (
        Invoice.objects.filter(is_archived=False)
        .exclude(status="cancelled")
        .annotate(awaiting_balance=balance_expression)
        .filter(awaiting_balance__gt=0)
        .filter(
            Q(opportunity__stage=AWAITING_PAYMENT_STAGE)
            | Q(quick_costing__opportunity__stage=AWAITING_PAYMENT_STAGE)
            | Q(costing_header__opportunity__stage=AWAITING_PAYMENT_STAGE)
            | Q(order__opportunity__stage=AWAITING_PAYMENT_STAGE)
        )
    )
    side = (side or "").upper()
    if side == "CA":
        invoice_queryset = invoice_queryset.filter(
            Q(customer__market="CA")
            | Q(opportunity__customer__market="CA")
            | Q(opportunity__lead__market="CA")
            | Q(quick_costing__opportunity__customer__market="CA")
            | Q(quick_costing__opportunity__lead__market="CA")
            | Q(costing_header__opportunity__customer__market="CA")
            | Q(costing_header__opportunity__lead__market="CA")
            | Q(order__opportunity__customer__market="CA")
            | Q(order__opportunity__lead__market="CA")
        )
    elif side == "BD":
        invoice_queryset = invoice_queryset.filter(
            Q(customer__market="BD")
            | Q(opportunity__customer__market="BD")
            | Q(opportunity__lead__market="BD")
            | Q(quick_costing__opportunity__customer__market="BD")
            | Q(quick_costing__opportunity__lead__market="BD")
            | Q(costing_header__opportunity__customer__market="BD")
            | Q(costing_header__opportunity__lead__market="BD")
            | Q(order__opportunity__customer__market="BD")
            | Q(order__opportunity__lead__market="BD")
        )

    customer_ids = set()
    opportunity_ids = set()
    totals = defaultdict(lambda: {"amount": Decimal("0")})
    invoice_count = 0
    for row in invoice_queryset.values(
        "id",
        "currency",
        "awaiting_balance",
        "customer_id",
        "opportunity_id",
        "opportunity__customer_id",
        "opportunity__lead__customer_id",
        "quick_costing__opportunity_id",
        "quick_costing__opportunity__customer_id",
        "quick_costing__opportunity__lead__customer_id",
        "costing_header__opportunity_id",
        "costing_header__opportunity__customer_id",
        "costing_header__opportunity__lead__customer_id",
        "order__opportunity_id",
        "order__opportunity__customer_id",
        "order__opportunity__lead__customer_id",
    ).distinct():
        opportunity_id = (
            row.get("opportunity_id")
            or row.get("quick_costing__opportunity_id")
            or row.get("costing_header__opportunity_id")
            or row.get("order__opportunity_id")
        )
        if opportunity_id:
            opportunity_ids.add(opportunity_id)
        customer_id = (
            row.get("customer_id")
            or row.get("opportunity__customer_id")
            or row.get("opportunity__lead__customer_id")
            or row.get("quick_costing__opportunity__customer_id")
            or row.get("quick_costing__opportunity__lead__customer_id")
            or row.get("costing_header__opportunity__customer_id")
            or row.get("costing_header__opportunity__lead__customer_id")
            or row.get("order__opportunity__customer_id")
            or row.get("order__opportunity__lead__customer_id")
        )
        if customer_id:
            customer_ids.add(customer_id)
        currency = (row.get("currency") or "CAD").upper()
        totals[currency]["amount"] += decimal_or_zero(row.get("awaiting_balance"))
        invoice_count += 1

    rows = currency_summary_rows(totals)
    for row in rows:
        row["display"] = format_finance_money(row["amount"], row["currency"])
    display = " / ".join(row["display"] for row in rows) or "-"
    return {
        "count": len(opportunity_ids),
        "customer_count": len(customer_ids),
        "invoice_count": invoice_count,
        "rows": rows,
        "display": display,
    }
