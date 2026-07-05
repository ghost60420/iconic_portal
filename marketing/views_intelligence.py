from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from marketing.forms import (
    MarketingCompetitorForm,
    MarketingContentIdeaForm,
    MarketingKeywordPlanForm,
    MarketingVideoIdeaForm,
)
from marketing.models import (
    MarketingCompetitor,
    MarketingContentIdea,
    MarketingKeywordPlan,
    MarketingVideoIdea,
    OAuthCredential,
    SocialAccount,
    WebsiteTrafficDaily,
)
from marketing.services.intelligence import build_data_source_status, build_internal_recommendations
from marketing.utils.activity import log_marketing_activity


FORM_CONFIG = {
    "keyword": (MarketingKeywordPlanForm, "Keyword idea saved."),
    "content": (MarketingContentIdeaForm, "Content idea saved."),
    "video": (MarketingVideoIdeaForm, "Video idea saved."),
    "competitor": (MarketingCompetitorForm, "Competitor saved."),
}


def _forms(post_data=None, active_name: str = "") -> dict:
    forms = {}
    for name, (form_class, _message) in FORM_CONFIG.items():
        forms[name] = form_class(post_data if name == active_name else None, prefix=name)
    return forms


def _calendar_rows(content_ideas, video_ideas) -> list[dict]:
    rows = [
        {
            "due_date": item.due_date,
            "title": item.title,
            "platform": item.get_target_platform_display(),
            "assigned_to": item.assigned_to,
            "status": item.get_status_display(),
            "kind": "Content",
        }
        for item in content_ideas
        if item.due_date and item.status not in {"published", "archived"}
    ]
    rows.extend(
        {
            "due_date": item.due_date,
            "title": item.video_title,
            "platform": item.get_platform_display(),
            "assigned_to": item.assigned_to,
            "status": item.get_status_display(),
            "kind": "Video",
        }
        for item in video_ideas
        if item.due_date and item.status not in {"published", "archived"}
    )
    return sorted(rows, key=lambda row: (row["due_date"], row["title"].casefold()))


def marketing_intelligence(request):
    if not getattr(settings, "MARKETING_ENABLED", False):
        raise Http404("Marketing disabled")

    active_form = (request.POST.get("form_name") or "").strip() if request.method == "POST" else ""
    forms = _forms(request.POST if request.method == "POST" else None, active_name=active_form)
    if request.method == "POST":
        config = FORM_CONFIG.get(active_form)
        if not config:
            messages.error(request, "Unknown marketing planner action.")
        else:
            form = forms[active_form]
            if form.is_valid():
                record = form.save(commit=False)
                if hasattr(record, "created_by_id"):
                    record.created_by = request.user
                record.save()
                log_marketing_activity(
                    user=request.user,
                    action=f"marketing_intelligence_{active_form}_created",
                    message=str(record),
                    model_label=record._meta.label,
                    object_id=record.pk,
                )
                messages.success(request, config[1])
                return redirect(f"{reverse('marketing_intelligence')}#{active_form}-planner")
            messages.error(request, "Please correct the highlighted planner fields.")

    keywords = list(MarketingKeywordPlan.objects.all()[:100])
    content_ideas = list(MarketingContentIdea.objects.select_related("assigned_to").all()[:100])
    video_ideas = list(MarketingVideoIdea.objects.select_related("assigned_to").all()[:100])
    competitors = list(MarketingCompetitor.objects.all()[:100])

    credentials = list(OAuthCredential.objects.filter(is_active=True).order_by("-updated_at"))
    credentials_by_platform = {}
    for credential in credentials:
        credentials_by_platform.setdefault(credential.platform, credential)
    social_counts = {
        row["platform"]: row["total"]
        for row in SocialAccount.objects.filter(is_active=True).values("platform").annotate(total=Count("id"))
    }
    website_rows = WebsiteTrafficDaily.objects.count()

    calendar_rows = _calendar_rows(content_ideas, video_ideas)
    blog_ideas = [item for item in content_ideas if item.content_type == "blog"]
    recommendations = build_internal_recommendations(
        keywords=keywords,
        content_ideas=content_ideas,
        video_ideas=video_ideas,
        competitors=competitors,
    )
    data_sources = build_data_source_status(
        credentials_by_platform=credentials_by_platform,
        social_counts=social_counts,
        website_rows=website_rows,
    )
    overview = {
        "keywords": len(keywords),
        "approved_keywords": sum(item.status == "approved" for item in keywords),
        "content_ideas": len(content_ideas),
        "videos": len(video_ideas),
        "competitors": len(competitors),
        "scheduled": len(calendar_rows),
    }

    return render(
        request,
        "marketing/intelligence.html",
        {
            "forms": forms,
            "overview": overview,
            "keywords": keywords,
            "content_ideas": content_ideas,
            "blog_ideas": blog_ideas,
            "video_ideas": video_ideas,
            "competitors": competitors,
            "calendar_rows": calendar_rows,
            "recommendations": recommendations,
            "data_sources": data_sources,
        },
    )
