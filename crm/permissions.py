# crm/permissions.py
from functools import wraps
from django.http import HttpResponseForbidden


def is_bd_user(user):
    return user.is_authenticated and user.groups.filter(name="BD_TEAM").exists()


def is_ca_user(user):
    return user.is_authenticated and user.groups.filter(name="CA_TEAM").exists()


def bd_blocked(view_func):
    """
    Block BD team only.
    Allow CA team, superuser, and any other non BD user.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user

        # If not logged in, do not decide permission here
        # login_required will handle redirect to login page
        if not user.is_authenticated:
            return HttpResponseForbidden("Login required")

        # Superuser always allowed
        if user.is_superuser:
            return view_func(request, *args, **kwargs)

        # If user is BD, block
        if is_bd_user(user):
            return HttpResponseForbidden("No access")

        # Everyone else allowed (CA or others)
        return view_func(request, *args, **kwargs)

    return wrapper