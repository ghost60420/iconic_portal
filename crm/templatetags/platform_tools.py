from django import template

from crm.services.employee_profiles import employee_display_name
from crm.services.platform_tools import record_timeline


register = template.Library()


@register.inclusion_tag("crm/platform/_record_timeline.html", takes_context=True)
def record_timeline_panel(context, module, record_id):
    request = context.get("request")
    rows = record_timeline(request.user, module, record_id) if request and record_id else []
    return {"timeline_rows": rows, "employee_display_name": employee_display_name}
