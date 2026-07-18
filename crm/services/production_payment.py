from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from crm.models import Invoice, InvoiceSettings
from crm.services.costing_currency import format_finance_money


DEFAULT_PRODUCTION_DEPOSIT_PERCENTAGE = Decimal("30.00")
DEFAULT_SAMPLE_PRODUCTION_DEPOSIT_PERCENTAGE = Decimal("100.00")
PERCENT_QUANT = Decimal("0.1")
MONEY_QUANT = Decimal("0.01")


def decimal_or_zero(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def clamp_percentage(value, fallback=DEFAULT_PRODUCTION_DEPOSIT_PERCENTAGE):
    if value in ("", None):
        value = fallback
    value = decimal_or_zero(value)
    if value < 0:
        return Decimal("0.00")
    if value > 100:
        return Decimal("100.00")
    return value


def format_percentage(value):
    amount = decimal_or_zero(value).quantize(PERCENT_QUANT, rounding=ROUND_HALF_UP)
    if amount == amount.to_integral_value():
        return f"{amount.quantize(Decimal('1'))}%"
    return f"{amount}%"


def production_deposit_percentage_for_invoice(invoice):
    invoice_type = (getattr(invoice, "invoice_type", "") or "").strip()
    invoice_market = (getattr(invoice, "invoice_market", "") or "").strip()
    settings_obj = InvoiceSettings.active()
    if invoice_type == "sample":
        value = getattr(settings_obj, "default_sample_deposit_percentage", None) if settings_obj else None
        return clamp_percentage(value, fallback=DEFAULT_SAMPLE_PRODUCTION_DEPOSIT_PERCENTAGE)
    if invoice_market == "bangladesh" and invoice_type == "sewing_charge":
        value = getattr(settings_obj, "default_bd_sewing_deposit_percentage", None) if settings_obj else None
        return clamp_percentage(value, fallback=DEFAULT_PRODUCTION_DEPOSIT_PERCENTAGE)
    value = getattr(settings_obj, "default_bulk_deposit_percentage", None) if settings_obj else None
    return clamp_percentage(value, fallback=DEFAULT_PRODUCTION_DEPOSIT_PERCENTAGE)


def invoice_queryset_for_opportunity(opportunity):
    if not opportunity or not getattr(opportunity, "pk", None):
        return Invoice.objects.none()
    return (
        Invoice.objects.select_related("opportunity", "quick_costing__opportunity", "costing_header__opportunity", "order__opportunity")
        .filter(
            Q(opportunity_id=opportunity.pk)
            | Q(quick_costing__opportunity_id=opportunity.pk)
            | Q(costing_header__opportunity_id=opportunity.pk)
            | Q(order__opportunity_id=opportunity.pk)
        )
        .distinct()
        .order_by("-issue_date", "-created_at", "-id")
    )


def production_payment_requirement(invoice):
    if not invoice:
        return {
            "invoice": None,
            "allowed": False,
            "reason": "missing_invoice",
            "message": "An invoice is required before moving to Production.",
        }

    currency = (getattr(invoice, "currency", "") or "CAD").upper()
    total = decimal_or_zero(getattr(invoice, "total_amount", None)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    paid = decimal_or_zero(getattr(invoice, "paid_amount", None)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if paid < 0:
        paid = Decimal("0.00")
    outstanding = (total - paid).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if outstanding < 0:
        outstanding = Decimal("0.00")

    required_percentage = production_deposit_percentage_for_invoice(invoice)
    required_amount = (total * required_percentage / Decimal("100")).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    paid_percentage = Decimal("0.0")
    if total > 0:
        paid_percentage = (paid * Decimal("100") / total).quantize(PERCENT_QUANT, rounding=ROUND_HALF_UP)
    remaining_to_start = (required_amount - paid).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if remaining_to_start < 0:
        remaining_to_start = Decimal("0.00")

    base = {
        "invoice": invoice,
        "allowed": False,
        "currency": currency,
        "invoice_total": total,
        "amount_paid": paid,
        "outstanding_balance": outstanding,
        "required_percentage": required_percentage,
        "required_amount": required_amount,
        "paid_percentage": paid_percentage,
        "remaining_to_start": remaining_to_start,
        "invoice_total_display": format_finance_money(total, currency),
        "amount_paid_display": format_finance_money(paid, currency),
        "outstanding_balance_display": format_finance_money(outstanding, currency),
        "required_amount_display": format_finance_money(required_amount, currency),
        "paid_percentage_display": format_percentage(paid_percentage),
        "required_percentage_display": format_percentage(required_percentage),
        "remaining_to_start_display": format_finance_money(remaining_to_start, currency),
    }

    if getattr(invoice, "is_archived", False):
        base.update(reason="archived_invoice", message="Archived invoices cannot move to Production.")
        return base
    if (getattr(invoice, "status", "") or "").lower() == "cancelled":
        base.update(reason="cancelled_invoice", message="Cancelled invoices cannot move to Production.")
        return base
    if total <= 0:
        base.update(reason="zero_invoice_total", message="Invoice total must be greater than 0 before moving to Production.")
        return base
    if paid >= required_amount:
        base.update(allowed=True, reason="deposit_met", message="")
        return base

    base.update(
        reason="deposit_not_met",
        message=(
            "Production requires a minimum deposit of "
            f"{base['required_percentage_display']}. Current payment is {base['paid_percentage_display']}."
        ),
    )
    return base


def select_production_payment_invoice(invoices):
    checks = []
    for invoice in invoices:
        check = production_payment_requirement(invoice)
        checks.append(check)
        if check["allowed"]:
            return invoice, check
    if checks:
        return None, checks[0]
    return None, production_payment_requirement(None)


def production_payment_progress_for_opportunity(opportunity):
    _invoice, check = select_production_payment_invoice(invoice_queryset_for_opportunity(opportunity))
    return check
