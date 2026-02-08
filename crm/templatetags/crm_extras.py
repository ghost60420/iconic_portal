from django import template

from crm.services.costing_currency import (
    format_bdt,
    format_cad,
    format_cad_from_bdt,
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
