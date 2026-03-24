import json
import traceback


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
