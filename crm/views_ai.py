# crm/views_ai.py

import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.db.utils import OperationalError
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from crm.ai.health import build_health_checks, run_and_store
from crm.ai.openai_client import ask_openai
from crm.ai.suggestions import (
    lead_suggestion,
    opportunity_suggestion,
    production_suggestion,
)

from crm.models import (
    AIHealthRun,
    Lead,
    Opportunity,
    Shipment,
    ProductionOrder,
)

from crm.models_email_outbox import OutboundEmailLog
from crm.permissions import get_access

try:
    from crm.models import AISystemLog  # type: ignore
except Exception:
    AISystemLog = None


def superuser_only(user):
    return bool(user and user.is_superuser)


def can_ai_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        access = get_access(user)
        return bool(getattr(access, "can_ai", False))
    except Exception:
        return False


def _safe_email(value: str) -> str:
    e = (value or "").strip()
    return e if ("@" in e and "." in e) else ""


def _ask_openai_safe(*, request, user, prompt_text, meta=None, feature=""):
    meta = meta or {}

    try:
        return ask_openai(
            request=request,
            user=user,
            prompt_text=prompt_text,
            meta=meta,
            feature=feature,
        )
    except TypeError:
        pass

    try:
        return ask_openai(
            request=request,
            user=user,
            prompt_text=prompt_text,
            meta=meta,
        )
    except TypeError:
        pass

    try:
        return ask_openai(
            user=user,
            prompt_text=prompt_text,
            meta=meta,
            feature=feature,
        )
    except TypeError:
        pass

    return ask_openai(
        user=user,
        prompt_text=prompt_text,
        meta=meta,
    )


def _send_and_log_email(*, request, lead, to_email: str, subject: str, body: str, message_type: str):
    to_email = _safe_email(to_email)
    subject = (subject or "").strip()[:255]
    body = (body or "").strip()

    log = OutboundEmailLog.objects.create(
        lead=lead,
        to_email=to_email,
        subject=subject,
        body=body,
        message_type=message_type,
        sent_ok=False,
        error="",
        created_by=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
    )

    if not to_email:
        log.error = "Lead email is missing"
        log.save(update_fields=["error"])
        return False, log.error

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
    from_email = from_email or None

    try:
        send_mail(
            subject=subject[:200],
            message=body,
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,
        )
        log.sent_ok = True
        log.save(update_fields=["sent_ok"])
        return True, ""
    except Exception as e:
        log.error = str(e)[:300]
        log.save(update_fields=["error"])
        return False, log.error


# -------------------------
# AI HUB PAGES
# -------------------------

@login_required
@user_passes_test(can_ai_user)
def ai_hub(request):
    try:
        report = build_health_checks()
        cards = report.get("cards", {})
        score = report.get("score", 0)
    except OperationalError:
        cards = {}
        score = 0

    return render(
        request,
        "crm/ai/ai_hub.html",
        {"cards": cards, "score": score},
    )


@login_required
@user_passes_test(can_ai_user)
def ai_assistant(request):
    today = timezone.localdate()
    last_30 = today - timedelta(days=30)

    if hasattr(Lead, "created_at"):
        leads_30 = Lead.objects.filter(created_at__date__gte=last_30).count()
    else:
        leads_30 = Lead.objects.count()

    if hasattr(Opportunity, "created_at"):
        opp_30 = Opportunity.objects.filter(created_at__date__gte=last_30).count()
    else:
        opp_30 = Opportunity.objects.count()

    return render(request, "crm/ai/ai_assistant.html", {"leads_30": leads_30, "opp_30": opp_30})


@require_POST
@login_required
@user_passes_test(can_ai_user)
def ai_assistant_ask(request):
    q = (request.POST.get("q") or "").strip()
    if not q:
        return JsonResponse({"ok": False, "error": "Empty question"}, status=400)

    today = timezone.localdate()
    last_30 = today - timedelta(days=30)

    if hasattr(Lead, "created_at"):
        leads_30 = Lead.objects.filter(created_at__date__gte=last_30).count()
    else:
        leads_30 = Lead.objects.count()

    if hasattr(Opportunity, "created_at"):
        opp_30 = Opportunity.objects.filter(created_at__date__gte=last_30).count()
    else:
        opp_30 = Opportunity.objects.count()

    prompt_text = (
        "CRM quick context:\n"
        f"Leads last 30 days: {leads_30}\n"
        f"Opportunities last 30 days: {opp_30}\n\n"
        "User question:\n"
        f"{q}\n\n"
        "Reply with a short helpful answer. If you suggest actions, list them as bullets."
    )

    try:
        answer = _ask_openai_safe(
            request=request,
            user=request.user,
            prompt_text=prompt_text,
            meta={"feature": "ai_assistant"},
            feature="ai_assistant",
        )
        return JsonResponse({"ok": True, "answer": answer})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)[:200]}, status=500)


@login_required
@user_passes_test(can_ai_user)
def ai_health_monitor(request):
    try:
        latest = AIHealthRun.objects.order_by("-created_at").first()
        if not latest:
            latest = run_and_store(created_by=request.user, notes="Auto first run")

        checks = list(latest.checks.order_by("id").values("name", "status", "detail"))
        report = build_health_checks()

        return render(
            request,
            "crm/ai/ai_health_monitor.html",
            {
                "score": latest.score,
                "checks": checks,
                "latest_run": latest,
                "cards": report.get("cards", {}),
                "recent_errors": report.get("recent_errors", []),
            },
        )

    except OperationalError:
        return render(
            request,
            "crm/ai/ai_health_monitor.html",
            {
                "score": 0,
                "checks": [],
                "latest_run": None,
                "cards": {},
                "recent_errors": [],
                "db_error": "AI health tables are not created yet. Run migrations.",
            },
        )


@login_required
@user_passes_test(can_ai_user)
def ai_system_status(request):
    try:
        report = build_health_checks()
        cards = report.get("cards", {})
        recent_errors = report.get("recent_errors", [])
        score = report.get("score", 0)
    except OperationalError:
        cards = {}
        recent_errors = []
        score = 0

    today = timezone.localdate()
    last_7 = today - timedelta(days=7)

    leads_7 = Lead.objects.filter(created_at__date__gte=last_7).count() if hasattr(Lead, "created_at") else None
    opp_7 = Opportunity.objects.filter(created_at__date__gte=last_7).count() if hasattr(Opportunity, "created_at") else None
    ship_count = Shipment.objects.count()

    warnings = []
    if leads_7 is not None and leads_7 == 0:
        warnings.append("No new leads in the last 7 days.")
    if opp_7 is not None and opp_7 == 0:
        warnings.append("No new opportunities in the last 7 days.")

    key_loaded = bool(getattr(settings, "OPENAI_API_KEY", "") or "")
    model_name = getattr(settings, "OPENAI_MODEL", "") or ""

    provider_ok = False
    provider_error = ""
    latency_ms = None

    if not key_loaded:
        provider_error = "OPENAI_API_KEY is missing"
    elif OpenAI is None:
        provider_error = "OpenAI package is not installed"
    else:
        try:
            start = time.time()
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            client.models.list()
            latency_ms = int((time.time() - start) * 1000)
            provider_ok = True
        except Exception as e:
            provider_ok = False
            provider_error = str(e)[:250]

    if AISystemLog:
        try:
            AISystemLog.objects.create(
                created_by=request.user,
                feature="system_status_view",
                provider="openai",
                model_name=model_name,
                level="info" if provider_ok else "error",
                message="System status viewed",
                latency_ms=latency_ms,
                error_detail=provider_error,
            )
        except Exception:
            pass

    return render(
        request,
        "crm/ai/ai_system_status.html",
        {
            "provider_name": cards.get("provider_name", "OpenAI"),
            "key_loaded": cards.get("key_loaded", key_loaded),
            "model_name": cards.get("model_name", model_name),
            "last_request_time": cards.get("last_request_time"),
            "errors_24h": cards.get("errors_24h", 0),
            "score": score,
            "recent_errors": recent_errors,
            "leads_7": leads_7,
            "opp_7": opp_7,
            "ship_count": ship_count,
            "warnings": warnings,
            "provider_ok": provider_ok,
            "provider_error": provider_error,
            "latency_ms": latency_ms,
        },
    )


# -------------------------
# SUGGESTION ENDPOINTS
# -------------------------

@require_POST
@login_required
@user_passes_test(can_ai_user)
def ai_lead_suggest(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    text = lead_suggestion(request=request, lead=lead)
    return JsonResponse({"ok": True, "answer": text})


@require_POST
@login_required
@user_passes_test(can_ai_user)
def ai_opportunity_suggest(request, pk):
    opp = get_object_or_404(Opportunity, pk=pk)
    text = opportunity_suggestion(request=request, opp=opp)
    return JsonResponse({"ok": True, "answer": text})


@require_POST
@login_required
@user_passes_test(can_ai_user)
def ai_production_suggest(request, pk):
    po = get_object_or_404(ProductionOrder, pk=pk)
    text = production_suggestion(request=request, po=po)
    return JsonResponse({"ok": True, "answer": text})


# -------------------------
# EMAIL SEND: THANK YOU + MEETING CONFIRM
# -------------------------

@require_POST
@login_required
@user_passes_test(can_ai_user)
def ai_lead_send_thankyou(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    to_email = _safe_email(getattr(lead, "email", ""))

    if not to_email:
        return JsonResponse({"ok": False, "error": "Lead email is missing."}, status=400)

    prompt = f"""
Write a short thank you email reply.

Rules:
Short
Friendly and professional
Say we received their message
Say our team will reply shortly
Ask for missing key detail like MOQ or tech pack or reference photos
Add WhatsApp contact: +1 604 500 6009

Lead:
Name: {getattr(lead, "contact_name", "")}
Brand: {getattr(lead, "account_brand", "")}
Product: {getattr(lead, "product_interest", "")}
""".strip()

    try:
        body = _ask_openai_safe(
            request=request,
            user=request.user,
            prompt_text=prompt,
            meta={"feature": "lead_thankyou", "lead_db_id": lead.id, "lead_id": getattr(lead, "lead_id", "")},
            feature="lead_thankyou",
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)[:200]}, status=500)

    name = (getattr(lead, "contact_name", "") or "").strip()
    subject = f"Thanks {name}, we received your request".strip()

    ok, err = _send_and_log_email(
        request=request,
        lead=lead,
        to_email=to_email,
        subject=subject,
        body=body,
        message_type="thank_you",
    )

    if not ok:
        return JsonResponse({"ok": False, "error": err or "Email failed"}, status=500)

    return JsonResponse({"ok": True, "subject": subject, "body": body})


@require_POST
@login_required
@user_passes_test(can_ai_user)
def ai_lead_send_meeting_confirm(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    to_email = _safe_email(getattr(lead, "email", ""))

    if not to_email:
        return JsonResponse({"ok": False, "error": "Lead email is missing."}, status=400)

    meeting_date = (request.POST.get("meeting_date") or "").strip()
    meeting_time = (request.POST.get("meeting_time") or "").strip()
    meeting_tz = (request.POST.get("meeting_tz") or "PST").strip()

    if not meeting_date or not meeting_time:
        return JsonResponse({"ok": False, "error": "meeting_date and meeting_time are required."}, status=400)

    prompt = f"""
Write a short meeting confirmation email.

Rules:
Short and clear
Confirm date and time
Ask them to reply if they need to reschedule
Add WhatsApp contact: +1 604 500 6009

Lead:
Name: {getattr(lead, "contact_name", "")}
Brand: {getattr(lead, "account_brand", "")}

Meeting:
Date: {meeting_date}
Time: {meeting_time} {meeting_tz}
""".strip()

    try:
        body = _ask_openai_safe(
            request=request,
            user=request.user,
            prompt_text=prompt,
            meta={"feature": "lead_meeting_confirm", "lead_db_id": lead.id, "lead_id": getattr(lead, "lead_id", "")},
            feature="lead_meeting_confirm",
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)[:200]}, status=500)

    subject = f"Meeting confirmed {meeting_date} at {meeting_time} {meeting_tz}".strip()

    ok, err = _send_and_log_email(
        request=request,
        lead=lead,
        to_email=to_email,
        subject=subject,
        body=body,
        message_type="meeting_confirm",
    )

    if not ok:
        return JsonResponse({"ok": False, "error": err or "Email failed"}, status=500)

    return JsonResponse({"ok": True, "subject": subject, "body": body})
