# crm/permissions.py

from functools import wraps
from django.http import HttpResponseForbidden
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import render

from .models_access import UserAccess
from .services.operations_permissions import get_access, operations_group_names, role_flag_decision

LIBRARY_FALLBACK_FLAGS = (
    "can_products",
    "can_fabrics",
    "can_accessories",
    "can_trims",
    "can_threads",
)

def bd_blocked(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user

        if not user.is_authenticated:
            return HttpResponseForbidden("Login required")

        if user.is_superuser:
            return view_func(request, *args, **kwargs)
        if "ceo" in operations_group_names(user):
            return view_func(request, *args, **kwargs)

        try:
            access = get_access(user)
        except (OperationalError, ProgrammingError):
            return HttpResponseForbidden("Access data not ready. Please run migrations.")

        if access.is_bd:
            return HttpResponseForbidden("No access")

        return view_func(request, *args, **kwargs)

    return wrapper


def _has_flag(access, flag_name):
    if flag_name == "can_library":
        if hasattr(access, "can_library"):
            return bool(getattr(access, "can_library", False))

        for f in LIBRARY_FALLBACK_FLAGS:
            if hasattr(access, f) and bool(getattr(access, f, False)):
                return True
        return False

    return bool(getattr(access, flag_name, False))


def can_view_internal_costing(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    role_decision = role_flag_decision(user, "can_view_internal_costing")
    if role_decision is not None:
        return role_decision
    try:
        access = get_access(user)
    except (OperationalError, ProgrammingError):
        return False
    return bool(getattr(access, "can_view_internal_costing", False))


def require_access(flag_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user

            if not user.is_authenticated:
                return HttpResponseForbidden("Login required")

            if user.is_superuser:
                return view_func(request, *args, **kwargs)

            try:
                access = get_access(user)
            except (OperationalError, ProgrammingError):
                return HttpResponseForbidden("Access data not ready. Please run migrations.")

            if flag_name == "can_accounting_ca" and access.is_bd and "ceo" not in operations_group_names(user):
                return HttpResponseForbidden("No access")

            role_decision = role_flag_decision(user, flag_name)
            if role_decision is not None:
                if role_decision:
                    return view_func(request, *args, **kwargs)
                return HttpResponseForbidden("No access")

            if not _has_flag(access, flag_name):
                return HttpResponseForbidden("No access")

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_any_access(*flag_names):
    """
    OR permission check.
    Pass if user has any flag in flag_names.
    If can_accounting_ca is included and user is BD, that flag is ignored.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user

            if not user.is_authenticated:
                return HttpResponseForbidden("Login required")

            if user.is_superuser:
                return view_func(request, *args, **kwargs)

            try:
                access = get_access(user)
            except (OperationalError, ProgrammingError):
                return HttpResponseForbidden("Access data not ready. Please run migrations.")

            for f in flag_names:
                if f == "can_accounting_ca" and access.is_bd and "ceo" not in operations_group_names(user):
                    continue
                role_decision = role_flag_decision(user, f)
                if role_decision is True:
                    return view_func(request, *args, **kwargs)
                if role_decision is False:
                    continue
                if _has_flag(access, f):
                    return view_func(request, *args, **kwargs)

            return HttpResponseForbidden("No access")

        return wrapper

    return decorator


def require_ceo_tools(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user

        if not user.is_authenticated:
            return HttpResponseForbidden("Login required")

        if user.is_superuser:
            return view_func(request, *args, **kwargs)

        try:
            access = get_access(user)
        except (OperationalError, ProgrammingError):
            return HttpResponseForbidden("Access data not ready. Please run migrations.")

        role_decision = role_flag_decision(user, "can_view_ceo_tools")
        if role_decision is True or (role_decision is None and _has_flag(access, "can_view_ceo_tools")):
            return view_func(request, *args, **kwargs)

        return render(request, "crm/access_denied.html", {"required_permission": "CEO tools"}, status=403)

    return wrapper
