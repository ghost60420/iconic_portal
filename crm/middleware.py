import json
import traceback

from django.http import HttpResponseForbidden

from crm.audit_context import reset_current_actor, set_current_actor


def _safe_get_user(request):
    try:
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            return user
    except Exception:
        return None
    return None


class ExceptionLoggingMiddleware:
    """
    Capture unhandled exceptions, log them to SystemActivityLog, then re-raise.
    This is low-risk and helps identify 500s in production.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except Exception as exc:
            try:
                from crm.models import SystemActivityLog

                meta = {
                    "path": getattr(request, "path", ""),
                    "method": getattr(request, "method", ""),
                    "user": getattr(_safe_get_user(request), "username", ""),
                    "error": str(exc),
                    "traceback": traceback.format_exc()[-4000:],
                }
                SystemActivityLog.objects.create(
                    actor=_safe_get_user(request),
                    area="server",
                    action="exception",
                    level="error",
                    path=(getattr(request, "path", "") or "")[:255],
                    method=(getattr(request, "method", "") or "")[:10],
                    message=str(exc)[:255],
                    meta_json=json.dumps(meta, default=str)[:4000],
                )
            except Exception:
                pass
            raise


class AuditActorMiddleware:
    """Expose the authenticated request actor to non-blocking audit signals."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_actor(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            reset_current_actor(token)


class ReadOnlyRoleMiddleware:
    """Enforce the additive Read Only role without changing existing view logic."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    ALLOWED_POST_PATHS = {"/accounts/logout/"}
    BLOCKED_DOWNLOAD_MARKERS = ("/export/", "/pdf/", ".pdf", "/excel/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False) and not user.is_superuser:
            from crm.permissions import operations_group_names

            if "read only" in operations_group_names(user):
                path = request.path.lower()
                if request.method not in self.SAFE_METHODS and path not in self.ALLOWED_POST_PATHS:
                    return HttpResponseForbidden("Read Only users cannot change CRM records.")
                if request.GET.get("export") or any(marker in path for marker in self.BLOCKED_DOWNLOAD_MARKERS):
                    return HttpResponseForbidden("Read Only users cannot export CRM records.")
        return self.get_response(request)
