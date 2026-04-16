import logging

from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .ai.lead_brain import build_iconic_ai_brain
from .models import Lead
from .views import _chatter_for_lead


logger = logging.getLogger(__name__)


def _safe_fetch(fetcher, fallback, label: str):
    try:
        return fetcher()
    except (OperationalError, ProgrammingError):
        logger.exception("iconic_ai_brain_refresh: failed to load %s", label)
        return fallback


@require_GET
def iconic_ai_brain_refresh(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    opportunities = _safe_fetch(
        lambda: lead.opportunities.all().order_by("-created_date", "-id"),
        [],
        "opportunities",
    )
    comments = _safe_fetch(lambda: _chatter_for_lead(lead), [], "comments")
    tasks = _safe_fetch(lambda: lead.tasks.all(), [], "tasks")
    activities = _safe_fetch(lambda: lead.activities.all(), [], "activities")
    insights = _safe_fetch(lambda: list(lead.ai_insights.all()[:1]), [], "insights")

    try:
        brain = build_iconic_ai_brain(
            lead=lead,
            opportunities=opportunities,
            comments=comments,
            tasks=tasks,
            activities=activities,
            insights=insights,
        )
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
