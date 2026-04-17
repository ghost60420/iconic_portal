import logging

from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .ai.lead_brain import build_iconic_ai_brain
from .ai.lead_brain_email_draft import build_iconic_ai_brain_email_draft
from .models import Lead
from .views import _chatter_for_lead


logger = logging.getLogger(__name__)


def _safe_fetch(fetcher, fallback, label: str):
    try:
        return fetcher()
    except (OperationalError, ProgrammingError):
        logger.exception("iconic_ai_brain_refresh: failed to load %s", label)
        return fallback


def _iconic_ai_brain_related_data(lead):
    opportunities = _safe_fetch(
        lambda: lead.opportunities.all().order_by("-created_date", "-id"),
        [],
        "opportunities",
    )
    comments = _safe_fetch(lambda: _chatter_for_lead(lead), [], "comments")
    tasks = _safe_fetch(lambda: lead.tasks.all(), [], "tasks")
    activities = _safe_fetch(lambda: lead.activities.all(), [], "activities")
    insights = _safe_fetch(lambda: list(lead.ai_insights.all()[:1]), [], "insights")
    return {
        "opportunities": opportunities,
        "comments": comments,
        "tasks": tasks,
        "activities": activities,
        "insights": insights,
    }


def _build_iconic_ai_brain_context(lead):
    return build_iconic_ai_brain(lead=lead, **_iconic_ai_brain_related_data(lead))


@require_GET
def iconic_ai_brain_refresh(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    try:
        brain = _build_iconic_ai_brain_context(lead)
    except Exception:
        logger.exception("iconic_ai_brain_refresh: build failed for lead %s", pk)
        return HttpResponse("Iconic AI Brain refresh failed.", status=500)

    return render(
        request,
        "crm/partials/iconic_ai_brain.html",
        {
            "brain": brain,
            "lead": lead,
        },
    )


@require_GET
def iconic_ai_brain_email_draft(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    to_email = (getattr(lead, "email", "") or "").strip()

    if not to_email:
        return JsonResponse({"error": "Lead email is missing."}, status=400)

    try:
        brain = _build_iconic_ai_brain_context(lead)
        draft = build_iconic_ai_brain_email_draft(lead=lead, brain=brain)
    except Exception:
        logger.exception("iconic_ai_brain_email_draft: build failed for lead %s", pk)
        return JsonResponse({"error": "Iconic AI Brain email draft failed."}, status=500)

    return JsonResponse(draft)
