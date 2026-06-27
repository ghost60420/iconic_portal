from django import template
try:
    from crm.models_access import UserAccess
except Exception:
    UserAccess = None

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


def _safe_access(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if UserAccess is None:
        return None
    if hasattr(user, "_crm_user_access_cached"):
        return user._crm_user_access_cached
    try:
        access = UserAccess.objects.filter(user=user).first()
        user._crm_user_access_cached = access
        return access
    except Exception:
        return None


@register.filter
def can_ai(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    access = _safe_access(user)
    return bool(getattr(access, "can_ai", False))


@register.filter
def can_marketing(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    access = _safe_access(user)
    return bool(getattr(access, "can_marketing", False))


@register.filter
def can_access(user, flag_name):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    access = _safe_access(user)
    try:
        from crm.permissions import role_flag_decision

        role_decision = role_flag_decision(user, (flag_name or "").strip())
        if role_decision is not None:
            if (
                (flag_name or "").strip() == "can_accounting_ca"
                and access
                and getattr(access, "is_bd", False)
                and not user.groups.filter(name="CEO").exists()
            ):
                return False
            return role_decision
    except Exception:
        pass
    if not access:
        return False
    flag_name = (flag_name or "").strip()
    if flag_name == "can_accounting_ca" and getattr(access, "is_bd", False):
        return False
    return bool(getattr(access, flag_name, False))


@register.filter
def is_current_url(request, url_name):
    try:
        return getattr(request.resolver_match, "url_name", "") == url_name
    except Exception:
        return False


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
