# crm/templatetags/crm_extras.py
from django import template

register = template.Library()

@register.filter
def get_item(d, key):
    """
    Safe dict lookup in templates.
    Usage: {{ my_dict|get_item:my_key }}
    """
    if d is None:
        return []
    return d.get(key, [])