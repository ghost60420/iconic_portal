import json

from crm.models import SystemActivityLog


def log_marketing_activity(
    *,
    user=None,
    action: str,
    message: str = "",
    model_label: str = "",
    object_id: str = "",
    level: str = "info",
    meta: dict | None = None,
):
    actor = user if user and getattr(user, "is_authenticated", False) else None
    try:
        SystemActivityLog.objects.create(
            actor=actor,
            area="marketing",
            action=(action or "event")[:50],
            level=level if level in {"info", "warn", "error"} else "info",
            path="",
            method="",
            model_label=model_label,
            object_id=str(object_id) if object_id else "",
            message=(message or "")[:255],
            meta_json=json.dumps(meta or {}, default=str)[:5000] if meta else "",
        )
    except Exception:
        pass


def log_marketing_sync_failure(*, platform: str, message: str, model_label: str = "", object_id: str = "", meta: dict | None = None):
    log_marketing_activity(
        action="marketing_sync_failure",
        level="error",
        message=f"{platform}: {message}",
        model_label=model_label,
        object_id=object_id,
        meta={"platform": platform, **(meta or {})},
    )
