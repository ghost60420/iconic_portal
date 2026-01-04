from crm.models import AISystemLog


def log_activity(
    *,
    user=None,
    feature="general",
    provider="openai",
    model_name="",
    level="info",
    message="",
    error_type="",
    error_detail="",
    latency_ms=None,
):
    """
    Central logger for all AI system activity.

    Use this for:
    - OpenAI calls
    - AI health checks
    - AI errors
    - AI background tasks
    """

    AISystemLog.objects.create(
        created_by=user,
        feature=feature,
        provider=provider,
        model_name=model_name,
        level=level,
        message=message,
        error_type=error_type,
        error_detail=error_detail,
        latency_ms=latency_ms,
    )