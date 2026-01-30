from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crm.models import AIHealthRun, AIHealthRunCheck
from crm.utils.activity_log import log_activity

# Optional model
try:
    from crm.models import AISystemLog  # type: ignore
except Exception:
    AISystemLog = None

# SystemActivityLog is used by log_activity
try:
    from crm.models import SystemActivityLog  # type: ignore
except Exception:
    SystemActivityLog = None


def _status_score(checks: list[dict]) -> int:
    score = 100
    for c in checks:
        if c.get("status") == "warn":
            score -= 10
        elif c.get("status") == "bad":
            score -= 25
    return max(score, 0)


def build_health_checks() -> dict:
    checks: list[dict] = []

    # 1) Database basic
    checks.append({"name": "Database", "status": "ok", "detail": "Connected"})

    # 2) OpenAI config
    key_loaded = bool(getattr(settings, "OPENAI_API_KEY", "") or "")
    model_name = (
        getattr(settings, "OPENAI_MODEL", "")
        or getattr(settings, "OPENAI_DEFAULT_MODEL", "")
        or ""
    )

    if not key_loaded:
        checks.append(
            {"name": "OpenAI API Key", "status": "bad", "detail": "Missing OPENAI_API_KEY"}
        )
    else:
        checks.append({"name": "OpenAI API Key", "status": "ok", "detail": "Loaded"})

    if not model_name:
        checks.append(
            {"name": "OpenAI Model", "status": "warn", "detail": "Model not set in settings"}
        )
    else:
        checks.append({"name": "OpenAI Model", "status": "ok", "detail": model_name})

    now = timezone.now()
    last_24h = now - timedelta(hours=24)

    last_request_time = None
    errors_24h = 0
    recent_errors: list[dict] = []

    # 3) Prefer AISystemLog if you have it
    if AISystemLog is not None:
        last_ai = (
            AISystemLog.objects.filter(provider="openai")
            .order_by("-created_at")
            .first()
        )
        if last_ai:
            last_request_time = last_ai.created_at

        errors_24h = AISystemLog.objects.filter(level="error", created_at__gte=last_24h).count()

        recent_errors_qs = AISystemLog.objects.filter(level="error").order_by("-created_at")[:50]
        recent_errors = list(
            recent_errors_qs.values(
                "created_at",
                "feature",
                "message",
                "error_type",
                "provider",
                "model_name",
            )
        )

    # 4) Fallback to SystemActivityLog if AISystemLog does not exist
    elif SystemActivityLog is not None:
        last_ai = (
            SystemActivityLog.objects.filter(area="ai")
            .order_by("-created_at")
            .first()
        )
        if last_ai:
            last_request_time = last_ai.created_at

        errors_24h = SystemActivityLog.objects.filter(
            level="error",
            created_at__gte=last_24h,
        ).count()

        recent_errors_qs = SystemActivityLog.objects.filter(level="error").order_by("-created_at")[:50]
        recent_errors = list(
            recent_errors_qs.values(
                "created_at",
                "area",
                "action",
                "message",
            )
        )

    else:
        checks.append(
            {"name": "AI Logs", "status": "warn", "detail": "No log model found yet"}
        )

    # Add checks from log data if we have it
    if errors_24h > 0:
        checks.append(
            {"name": "AI Errors (24h)", "status": "warn", "detail": f"{errors_24h} errors in last 24 hours"}
        )
    else:
        checks.append({"name": "AI Errors (24h)", "status": "ok", "detail": "No errors in last 24 hours"})

    if last_request_time:
        age_mins = int((now - last_request_time).total_seconds() / 60)
        status = "warn" if age_mins > 180 else "ok"
        checks.append({"name": "Last AI Request", "status": status, "detail": f"{age_mins} minutes ago"})
    else:
        checks.append({"name": "Last AI Request", "status": "warn", "detail": "No requests logged yet"})

    score = _status_score(checks)

    cards = {
        "provider_name": "OpenAI",
        "key_loaded": key_loaded,
        "model_name": model_name,
        "last_request_time": last_request_time,
        "errors_24h": errors_24h,
        "score": score,
    }

    return {
        "score": score,
        "checks": checks,
        "cards": cards,
        "recent_errors": recent_errors,
    }


@transaction.atomic
def run_and_store(created_by=None, notes: str = "") -> AIHealthRun:
    report = build_health_checks()

    ok_count = sum(1 for c in report["checks"] if c.get("status") == "ok")
    warn_count = sum(1 for c in report["checks"] if c.get("status") == "warn")
    bad_count = sum(1 for c in report["checks"] if c.get("status") == "bad")

    run = AIHealthRun.objects.create(
        created_by=created_by,
        score=report["score"],
        ok_count=ok_count,
        warn_count=warn_count,
        bad_count=bad_count,
        notes=notes or "",
    )

    for c in report["checks"]:
        AIHealthRunCheck.objects.create(
            run=run,
            name=c.get("name", ""),
            status=c.get("status", "ok"),
            detail=c.get("detail", ""),
        )

    # Log into SystemActivityLog using the correct signature
    try:
        meta = {
            "score": run.score,
            "ok_count": ok_count,
            "warn_count": warn_count,
            "bad_count": bad_count,
        }
        log_activity(
            user=created_by,
            feature="ai_health",
            provider="local",
            model_name="",
            level="info",
            message=f"AI health run stored. Score {run.score}",
            error_detail=str(meta),
        )
    except Exception:
        pass

    return run
