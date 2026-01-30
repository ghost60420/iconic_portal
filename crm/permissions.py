# crm/permissions.py

from functools import wraps
from django.http import HttpResponseForbidden

from .models_access import UserAccess

LIBRARY_FALLBACK_FLAGS = (
    "can_products",
    "can_fabrics",
    "can_accessories",
    "can_trims",
    "can_threads",
)


def get_access(user):
    access, _ = UserAccess.objects.get_or_create(user=user)
    return access


def bd_blocked(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user

        if not user.is_authenticated:
            return HttpResponseForbidden("Login required")

        if user.is_superuser:
            return view_func(request, *args, **kwargs)

        access = get_access(user)

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


def require_access(flag_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user

            if not user.is_authenticated:
                return HttpResponseForbidden("Login required")

            if user.is_superuser:
                return view_func(request, *args, **kwargs)

            access = get_access(user)

            if flag_name == "can_accounting_ca" and access.is_bd:
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

            access = get_access(user)

            for f in flag_names:
                if f == "can_accounting_ca" and access.is_bd:
                    continue
                if _has_flag(access, f):
                    return view_func(request, *args, **kwargs)

            return HttpResponseForbidden("No access")

        return wrapper

    return decorator