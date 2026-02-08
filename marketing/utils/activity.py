from crm.models import SystemActivityLog


def log_marketing_activity(*, user, action: str, message: str = "", model_label: str = "", object_id: str = ""):
    if not user or not getattr(user, "is_authenticated", False):
        return
    try:
        SystemActivityLog.objects.create(
            actor=user,
            area="marketing",
            action=action,
            level="info",
            path="",
            method="",
            model_label=model_label,
            object_id=str(object_id) if object_id else "",
            message=message,
        )
    except Exception:
        pass
