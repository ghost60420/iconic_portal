from django import template

from crm.services.costing_currency import (
    format_bdt,
    format_cad,
    format_cad_from_bdt,
    format_compact_finance_money,
    format_finance_money,
)

register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return ""
    return mapping.get(key, "")


@register.filter(name="production_po")
def production_po_filter(value):
    if hasattr(value, "purchase_order_number"):
        return value.purchase_order_number
    from crm.models import ProductionOrder

    return ProductionOrder.format_purchase_order_number(value)


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
    return format_compact_finance_money(value, currency)
