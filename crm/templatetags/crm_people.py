import re

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from crm.services.chatter_mentions import MENTION_RE
from crm.services.employee_profiles import (
    can_manage_employees,
    can_manage_roles,
    can_view_team_performance,
    employee_display_name,
)
from crm.services.operations_permissions import ROLE_SALES, has_operations_role


register = template.Library()


@register.filter
def employee_name(user):
    return employee_display_name(user)


@register.filter
def employee_initials(user):
    name = employee_display_name(user).strip()
    return (name[:1] or "?").upper()


@register.filter
def chatter_author(value):
    value = (value or "User").strip()
    return "Former Employee" if "@" in value else value


@register.simple_tag
def can_manage_people(user):
    return can_manage_employees(user)


@register.simple_tag
def can_manage_role_assignments(user):
    return can_manage_roles(user)


@register.simple_tag
def has_sales_profile(user):
    return has_operations_role(user, ROLE_SALES)


@register.simple_tag
def can_view_team_dashboard(user):
    return can_view_team_performance(user)


@register.filter(needs_autoescape=True)
def highlight_mentions(value, autoescape=True):
    escape = conditional_escape if autoescape else str
    escaped = escape(value or "")
    highlighted = MENTION_RE.sub(
        lambda match: f'<span class="crm-mention">@{match.group(1)}</span>',
        escaped,
    )
    return mark_safe(highlighted)
