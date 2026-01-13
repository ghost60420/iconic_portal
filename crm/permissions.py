# crm/permissions.py

from functools import wraps

from django.http import HttpResponseForbidden

from .models_access import UserAccess


def get_access(user):
    """
    Always return a UserAccess row for a logged in user.
    """
    access, _ = UserAccess.objects.get_or_create(user=user)
    return access


def bd_blocked(view_func):
    """
    Block BD role users only.
    CA role users (and superusers) can pass.

    Important:
    Do not redirect to login here.
    Use login_required in urls.py.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user

        # login_required should handle this
        if not user.is_authenticated:
            return HttpResponseForbidden("Login required")

        if user.is_superuser:
            return view_func(request, *args, **kwargs)

        access = get_access(user)

        # BD is blocked
        if access.is_bd:
            return HttpResponseForbidden("No access")

        return view_func(request, *args, **kwargs)

    return wrapper


def require_access(flag_name):
    """
    Checkmark permission guard.
    Example usage:
      @login_required
      @require_access("can_leads")
      def leads_list(...):

    Important:
    Do not redirect to login here.
    Use login_required in urls.py.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user

            # login_required should handle this
            if not user.is_authenticated:
                return HttpResponseForbidden("Login required")

            if user.is_superuser:
                return view_func(request, *args, **kwargs)

            access = get_access(user)

            # Hard safety rule: BD never allowed to CA accounting
            if flag_name == "can_accounting_ca" and access.is_bd:
                return HttpResponseForbidden("No access")

            if not getattr(access, flag_name, False):
                return HttpResponseForbidden("No access")

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator