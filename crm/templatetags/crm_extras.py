from decimal import Decimal, InvalidOperation

from django import template

from crm.services.costing_currency import (
    format_bdt,
    format_cad,
    format_cad_from_bdt,
    format_finance_money,
)

register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return ""
    return mapping.get(key, "")


@register.filter(name="format_bdt")
def format_bdt_filter(value):
    return format_bdt(value)


@register.filter(name="format_cad")
def format_cad_filter(value):
    return format_cad(value)


@register.filter(name="format_cad_from_bdt")
def format_cad_from_bdt_filter(value, exchange_rate):
    formatted = format_cad_from_bdt(value, exchange_rate)
    return formatted or ""


@register.filter(name="finance_money")
def finance_money_filter(value, currency):
    return format_finance_money(value, currency)


@register.filter(name="compact_money")
def compact_money_filter(value, currency):
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")

    absolute = abs(amount)
    suffix = ""
    divisor = Decimal("1")
    for threshold, candidate_suffix in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if absolute >= threshold:
            divisor = threshold
            suffix = candidate_suffix
            break

    scaled = absolute / divisor
    if suffix:
        decimal_places = 0 if scaled >= 100 else 1 if scaled >= 10 else 2
        rendered = f"{scaled:.{decimal_places}f}".rstrip("0").rstrip(".")
    else:
        rendered = f"{scaled:,.2f}".rstrip("0").rstrip(".") or "0"

    sign = "-" if amount < 0 else ""
    code = str(currency or "").upper()
    if code == "CAD":
        return f"CAD {sign}${rendered}{suffix}"
    if code == "USD":
        return f"USD {sign}${rendered}{suffix}"
    if code == "BDT":
        return f"{sign}৳{rendered}{suffix}"
    if code:
        return f"{code} {sign}{rendered}{suffix}"
    return f"{sign}{rendered}{suffix}"
