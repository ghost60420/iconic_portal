from django import template

register = template.Library()


def _norm(s):
    return (s or "").strip().lower()


def _group_names(user):
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    try:
        return {_norm(g.name) for g in user.groups.all()}
    except Exception:
        return set()


def _in_any_group(user, names_csv):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    want = {_norm(x) for x in (names_csv or "").split(",") if _norm(x)}
    if not want:
        return False

    have = _group_names(user)
    return any(x in have for x in want)


@register.filter
def in_group(user, group_name):
    return _in_any_group(user, group_name)


# -------------------------
# Filters (so templates can do: request.user|is_ca)
# -------------------------
@register.filter
def is_ca(user):
    return _in_any_group(user, "CA,Canada")


@register.filter
def is_bd(user):
    return _in_any_group(user, "BD,Bangladesh")


@register.filter
def can_view_accounting_ca(user):
    return _in_any_group(user, "CA,Canada")


@register.filter
def can_view_accounting_bd(user):
    # BD can see BD, CA can also see BD
    return _in_any_group(user, "BD,Bangladesh,CA,Canada")


@register.filter
def can_edit_bd_entries(user):
    # Change this later if you want BD not to edit
    return _in_any_group(user, "BD,Bangladesh,CA,Canada")


# -------------------------
# Tags (optional, if you want to use: {% is_ca as ok %}
# -------------------------
@register.simple_tag(takes_context=True)
def is_ca_tag(context):
    user = context.get("request").user if context.get("request") else None
    return is_ca(user)


@register.simple_tag(takes_context=True)
def is_bd_tag(context):
    user = context.get("request").user if context.get("request") else None
    return is_bd(user)