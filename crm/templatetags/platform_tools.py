from decimal import Decimal, InvalidOperation

from django import template

from crm.services.costing_currency import format_finance_money
from crm.services.employee_profiles import employee_display_name
from crm.services.platform_tools import record_timeline


register = template.Library()


_MONEY_FIELD_TOKENS = ("amount", "balance", "cost", "price", "profit", "revenue", "shipping", "subtotal", "tax", "total", "value")


def _audit_currency(module, field_name, context):
    field = (field_name or "").lower()
    if "usd" in field:
        return "USD"
    if "bdt" in field or module in {"opportunities", "production"}:
        return "BDT"
    record = context.get("invoice") or context.get("costing") or context.get("quick_costing")
    return (getattr(record, "currency", "") or "CAD").upper()


def _audit_display(value, module, field_name, context):
    if not value or "rate" in (field_name or "").lower() or "percent" in (field_name or "").lower():
        return value
    if not any(token in (field_name or "").lower() for token in _MONEY_FIELD_TOKENS):
        return value
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return value
    return format_finance_money(amount, _audit_currency(module, field_name, context))


@register.inclusion_tag("crm/platform/_record_timeline.html", takes_context=True)
def record_timeline_panel(context, module, record_id):
    request = context.get("request")
    rows = list(record_timeline(request.user, module, record_id)) if request and record_id else []
    for row in rows:
        row.previous_display_value = _audit_display(row.previous_value, module, row.field_name, context)
        row.new_display_value = _audit_display(row.new_value, module, row.field_name, context)
    return {"timeline_rows": rows, "employee_display_name": employee_display_name}
