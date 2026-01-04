from functools import wraps
from django.http import HttpResponseForbidden


def _norm(s):
    return (s or "").strip().lower()


def _in_group(user, names_csv):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    want = {_norm(x) for x in (names_csv or "").split(",") if _norm(x)}
    if not want:
        return False

    have = {_norm(g.name) for g in user.groups.all()}
    return any(x in have for x in want)


def bd_required(view_func):
    """
    Allow BD team, CA team, or superuser.
    This is for BD pages where CA can also see BD side.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        u = request.user
        if _in_group(u, "BD,Bangladesh") or _in_group(u, "CA,Canada") or getattr(u, "is_superuser", False):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden("No permission")
    return _wrapped


def ca_required(view_func):
    """
    Allow CA team or superuser only.
    This is for CA pages.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        u = request.user
        if _in_group(u, "CA,Canada") or getattr(u, "is_superuser", False):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden("No permission")
    return _wrapped


def bd_only_required(view_func):
    """
    Allow BD team or superuser only.
    Use this if you want CA blocked from a BD page.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        u = request.user
        if _in_group(u, "BD,Bangladesh") or getattr(u, "is_superuser", False):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden("No permission")
    return _wrapped