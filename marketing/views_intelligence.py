from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from marketing.forms import (
    MarketingBlogPlanForm,
    MarketingCompetitorForm,
    MarketingContentIdeaForm,
    MarketingKeywordGenerationForm,
    MarketingKeywordPlanForm,
    MarketingTaskForm,
    MarketingTrendEntryForm,
    MarketingVideoIdeaForm,
)
from marketing.integrations import integration_statuses
from marketing.models import (
    MarketingCompetitor,
    MarketingContentIdea,
    MarketingKeywordGeneration,
    MarketingKeywordPlan,
    MarketingTask,
    MarketingTrendEntry,
    MarketingVideoIdea,
)
from marketing.services.intelligence import (
    build_assistant_answers,
    build_marketing_scores,
    dashboard_snapshot,
    generate_keyword_recommendations,
)
from marketing.services.intelligence_reports import REPORT_LABELS, build_marketing_report
from marketing.utils.activity import log_marketing_activity


FORM_CONFIG = {
    "keyword": (MarketingKeywordPlanForm, "SEO opportunity saved."),
    "content": (MarketingContentIdeaForm, "Content idea saved."),
    "blog": (MarketingBlogPlanForm, "Blog brief saved."),
    "video": (MarketingVideoIdeaForm, "Video plan saved."),
    "competitor": (MarketingCompetitorForm, "Competitor saved."),
    "keyword_generation": (MarketingKeywordGenerationForm, "Keyword recommendations generated and saved."),
    "trend": (MarketingTrendEntryForm, "Manual trend saved."),
    "task": (MarketingTaskForm, "Marketing task created."),
}

KANBAN_STATUSES = [
    ("idea", "Idea"),
    ("assigned", "Assigned"),
    ("in_progress", "In progress"),
    ("review", "Review"),
    ("ready", "Ready"),
    ("scheduled", "Scheduled"),
    ("published", "Published"),
    ("archived", "Archived"),
]
STATUS_TO_MODEL = {
    "idea": "idea",
    "assigned": "assigned",
    "in_progress": "in_progress",
    "review": "ready_for_review",
    "ready": "approved",
    "scheduled": "scheduled",
    "published": "published",
    "archived": "archived",
}
MODEL_TO_STATUS = {
    "idea": "idea",
    "assigned": "assigned",
    "in_progress": "in_progress",
    "ready_for_review": "review",
    "approved": "ready",
    "scheduled": "scheduled",
    "published": "published",
    "archived": "archived",
}


def _assignee_choices() -> list[tuple[str, str]]:
    rows = get_user_model().objects.filter(is_active=True).values_list("id", "first_name", "last_name", "username")
    return [("", "---------")] + [
        (str(pk), " ".join(part for part in (first, last) if part).strip() or username)
        for pk, first, last, username in rows
    ]


def _forms(post_data=None, active_name: str = "", *, assignee_choices=None, source_choices=None) -> dict:
    forms = {}
    for name, (form_class, _message) in FORM_CONFIG.items():
        kwargs = {"prefix": name}
        if name in {"content", "blog", "video"}:
            kwargs["assignee_choices"] = assignee_choices
        if name == "task":
            kwargs["assignee_choices"] = assignee_choices
            kwargs["source_choices"] = source_choices
        forms[name] = form_class(post_data if name == active_name else None, **kwargs)
    return forms


def _month_bounds(raw_month: str):
    today = timezone.localdate()
    try:
        selected = datetime.strptime(raw_month, "%Y-%m").date().replace(day=1) if raw_month else today.replace(day=1)
    except ValueError:
        selected = today.replace(day=1)
    next_month = (selected.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    aware_start = timezone.make_aware(datetime.combine(selected, time.min))
    aware_end = timezone.make_aware(datetime.combine(next_month, time.min))
    return selected, month_end, aware_start, aware_end


def _calendar_item(item, item_type: str) -> dict:
    is_video = item_type == "video"
    return {
        "id": item.pk,
        "item_type": item_type,
        "title": item.video_title if is_video else item.title,
        "platform": item.get_platform_display() if is_video else item.get_target_platform_display(),
        "due_date": item.due_date,
        "assigned_to": item.assigned_to,
        "status": MODEL_TO_STATUS.get(item.status, "idea"),
    }


def _calendar_context(content_ideas, video_ideas, month_start: date, month_end: date) -> dict:
    all_items = [_calendar_item(item, "content") for item in content_ideas]
    all_items.extend(_calendar_item(item, "video") for item in video_ideas)
    items = [item for item in all_items if item["due_date"] and month_start <= item["due_date"] <= month_end]
    items.sort(key=lambda item: (item["due_date"], item["title"].casefold()))
    by_date = {}
    for item in items:
        by_date.setdefault(item["due_date"], []).append(item)
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
        weeks.append([{"date": day, "in_month": day.month == month_start.month, "items": by_date.get(day, [])} for day in week])
    today = timezone.localdate()
    weekly = [item for item in items if today <= item["due_date"] <= today + timedelta(days=7)]
    upcoming = [item for item in items if item["due_date"] >= today]
    kanban = [{"key": key, "label": label, "items": [item for item in all_items if item["status"] == key]} for key, label in KANBAN_STATUSES]
    return {"items": items, "upcoming": upcoming, "weeks": weeks, "weekly": weekly, "kanban": kanban}


def _recent_content(content_ideas, video_ideas):
    rows = [
        {
            "display_title": item.title,
            "display_platform": item.target_platform,
            "item_type": "Content",
            "status": item.status,
            "updated_at": item.updated_at,
        }
        for item in content_ideas
    ]
    rows.extend(
        {
            "display_title": item.video_title,
            "display_platform": item.platform,
            "item_type": "Video",
            "status": item.status,
            "updated_at": item.updated_at,
        }
        for item in video_ideas
    )
    return sorted(rows, key=lambda item: item["updated_at"], reverse=True)[:8]


def _keyword_queryset(request):
    queryset = MarketingKeywordPlan.objects.all()
    query = (request.GET.get("q") or "").strip()
    if query:
        queryset = queryset.filter(Q(keyword__icontains=query) | Q(suggested_article__icontains=query) | Q(suggested_video__icontains=query))
    for parameter, field in (
        ("country", "target_country"),
        ("category", "product_category"),
        ("intent", "search_intent"),
        ("status", "status"),
    ):
        value = (request.GET.get(parameter) or "").strip()
        if value:
            queryset = queryset.filter(**{field: value})
    sort_map = {
        "keyword": "keyword",
        "-keyword": "-keyword",
        "searches": "monthly_search_estimate",
        "-searches": "-monthly_search_estimate",
        "priority": "priority",
        "difficulty": "difficulty_estimate",
        "newest": "-created_at",
    }
    return queryset.order_by(sort_map.get(request.GET.get("sort"), "-created_at"))


def _write_activity(request, record, action: str):
    log_marketing_activity(
        user=request.user,
        action=action,
        message=str(record),
        model_label=record._meta.label,
        object_id=record.pk,
    )


def _update_status(request):
    item_type = request.POST.get("item_type")
    status_key = request.POST.get("status")
    model_status = STATUS_TO_MODEL.get(status_key)
    model = MarketingVideoIdea if item_type == "video" else MarketingContentIdea if item_type == "content" else None
    if not model or not model_status:
        return JsonResponse({"ok": False, "message": "Invalid content status update."}, status=400)
    record = get_object_or_404(model, pk=request.POST.get("item_id"))
    record.status = model_status
    record.save(update_fields=["status", "updated_at"])
    _write_activity(request, record, "marketing_intelligence_status_updated")
    return JsonResponse({"ok": True, "status": status_key})


def _task_source_choices(keyword_items, content_items):
    choices = [("", "No source")]
    choices.extend((f"keyword:{item.pk}", f"Keyword — {item.keyword}") for item in keyword_items)
    choices.extend((f"content:{item.pk}", f"Content — {item.title}") for item in content_items[:50])
    return choices


def _intelligence_source_statuses():
    """Keep unavailable future adapters out of live intelligence source cards."""
    return [source for source in integration_statuses() if source.key != "google_trends"]


def _create_task(request, form):
    if not form.is_valid():
        messages.error(request, "Please correct the highlighted task fields.")
        return None
    source_keyword = None
    source_content = None
    source = form.cleaned_data.get("source") or ""
    if source.startswith("keyword:"):
        source_keyword = get_object_or_404(MarketingKeywordPlan, pk=source.split(":", 1)[1])
    elif source.startswith("content:"):
        source_content = get_object_or_404(MarketingContentIdea, pk=source.split(":", 1)[1])
    assigned_to = None
    if form.cleaned_data.get("assigned_to"):
        assigned_to = get_object_or_404(get_user_model(), pk=form.cleaned_data["assigned_to"], is_active=True)
    record = MarketingTask.objects.create(
        title=form.cleaned_data["title"],
        source_keyword=source_keyword,
        source_content=source_content,
        assigned_to=assigned_to,
        due_date=form.cleaned_data.get("due_date"),
        priority=form.cleaned_data["priority"],
        platform=form.cleaned_data["platform"],
        notes=form.cleaned_data.get("notes", ""),
        created_by=request.user,
    )
    _write_activity(request, record, "marketing_intelligence_task_created")
    messages.success(request, "Marketing task created.")
    return redirect(f"{reverse('marketing_intelligence')}#task-generator")


def _handle_post(request, forms, *, can_edit: bool, can_create: bool):
    active_form = (request.POST.get("form_name") or "").strip()
    if active_form in {"status_update", "competitor"} and not can_edit:
        return HttpResponseForbidden("Only Admin and Marketing Manager users can edit marketing operations.")
    if active_form not in {"status_update", "competitor"} and not can_create:
        return HttpResponseForbidden("This marketing role is read only.")
    if active_form == "status_update":
        return _update_status(request)
    if active_form == "task":
        return _create_task(request, forms["task"])
    if active_form == "generate_video":
        keyword = get_object_or_404(MarketingKeywordPlan, pk=request.POST.get("keyword_id"))
        record = MarketingVideoIdea.objects.create(
            video_title=keyword.suggested_video or f"What buyers should know about {keyword.keyword}",
            hook=f"Before you choose a supplier for {keyword.keyword}, check these three details.",
            thumbnail_text=keyword.keyword.title(),
            opening=f"Here is what brands need to know about {keyword.keyword}.",
            main_talking_points="Buyer problem\nManufacturing insight\nQuality checkpoint\nRecommended next step",
            closing_cta="Contact Iconic Apparel to plan your product.",
            video_length="60 seconds",
            target_keyword=keyword.keyword,
            product_category=keyword.product_category,
            created_by=request.user,
        )
        _write_activity(request, record, "marketing_intelligence_video_generated")
        messages.success(request, "Internal video plan generated and saved.")
        return redirect(f"{reverse('marketing_intelligence')}#video-planner")
    config = FORM_CONFIG.get(active_form)
    if not config:
        messages.error(request, "Unknown marketing planner action.")
        return None
    form = forms[active_form]
    if not form.is_valid():
        messages.error(request, "Please correct the highlighted planner fields.")
        return None
    record = form.save(commit=False)
    if hasattr(record, "created_by_id"):
        record.created_by = request.user
    if isinstance(record, MarketingKeywordGeneration):
        generated = generate_keyword_recommendations(
            country=record.country,
            industry=record.industry,
            product=record.product,
            target_customer=record.target_customer,
        )
        for field, values in generated.items():
            setattr(record, field, values)
    record.save()
    _write_activity(request, record, f"marketing_intelligence_{active_form}_created")
    messages.success(request, config[1])
    if isinstance(record, MarketingKeywordGeneration):
        return redirect(f"{reverse('marketing_intelligence')}?generation={record.pk}#idea-generator")
    return redirect(f"{reverse('marketing_intelligence')}#{active_form.replace('_generation', '')}-planner")


def marketing_intelligence(request):
    if not getattr(settings, "MARKETING_ENABLED", False):
        raise Http404("Marketing disabled")

    can_edit = bool(getattr(request, "marketing_can_edit", False) or request.user.is_superuser)
    can_create = bool(getattr(request, "marketing_can_create", False) or can_edit)

    active_form = (request.POST.get("form_name") or "").strip() if request.method == "POST" else ""
    month_start, month_end, aware_start, aware_end = _month_bounds(request.GET.get("month", ""))
    summary = dashboard_snapshot(month_start=aware_start, month_end=aware_end)
    keyword_page = Paginator(_keyword_queryset(request), 25).get_page(request.GET.get("page"))
    calendar_filter = Q(due_date__range=(month_start, month_end)) | ~Q(status__in=("published", "archived"))
    content_ideas = list(
        MarketingContentIdea.objects.filter(calendar_filter)
        .select_related("assigned_to")
        .order_by(F("due_date").asc(nulls_last=True), "-updated_at")[:150]
    )
    video_ideas = list(
        MarketingVideoIdea.objects.filter(calendar_filter)
        .select_related("assigned_to")
        .order_by(F("due_date").asc(nulls_last=True), "-updated_at")[:150]
    )
    competitor_page = Paginator(MarketingCompetitor.objects.filter(is_active=True), 20).get_page(
        request.GET.get("competitor_page")
    )
    trend_entries = list(MarketingTrendEntry.objects.all()[:20])
    tasks = list(MarketingTask.objects.select_related("assigned_to", "source_keyword", "source_content")[:30])
    source_choices = _task_source_choices(keyword_page.object_list, content_ideas)
    assignee_choices = _assignee_choices() if can_create else []
    forms = _forms(
        request.POST if request.method == "POST" else None,
        active_name=active_form,
        assignee_choices=assignee_choices,
        source_choices=source_choices,
    )
    if request.method == "POST":
        response = _handle_post(request, forms, can_edit=can_edit, can_create=can_create)
        if response:
            return response

    generation_id = (request.GET.get("generation") or "").strip()
    recent_generation = (
        MarketingKeywordGeneration.objects.filter(pk=generation_id).first()
        if generation_id.isdigit()
        else None
    )
    calendar_context = _calendar_context(content_ideas, video_ideas, month_start, month_end)
    assistant = build_assistant_answers(
        keywords=keyword_page.object_list,
        content_ideas=content_ideas,
        video_ideas=video_ideas,
        summary=summary,
    )

    filter_params = request.GET.copy()
    filter_params.pop("page", None)
    return render(
        request,
        "marketing/intelligence.html",
        {
            "forms": forms,
            "can_edit": can_edit,
            "can_create": can_create,
            "summary": summary,
            "scores": build_marketing_scores(summary),
            "keyword_page": keyword_page,
            "filter_query": filter_params.urlencode(),
            "content_ideas": content_ideas,
            "video_ideas": video_ideas,
            "competitor_page": competitor_page,
            "trend_entries": trend_entries,
            "tasks": tasks,
            "recent_generation": recent_generation,
            "recent_content": _recent_content(content_ideas, video_ideas),
            "calendar": calendar_context,
            "selected_month": month_start,
            "assistant_answers": assistant,
            "integration_statuses": _intelligence_source_statuses(),
            "kanban_statuses": KANBAN_STATUSES,
            "report_labels": REPORT_LABELS,
        },
    )


def _editor_required(request):
    return bool(getattr(request, "marketing_can_edit", False) or request.user.is_superuser)


def edit_keyword(request, pk):
    if not _editor_required(request):
        return HttpResponseForbidden("Only Admin and Marketing Manager users can edit SEO opportunities.")
    record = get_object_or_404(MarketingKeywordPlan, pk=pk)
    form = MarketingKeywordPlanForm(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        _write_activity(request, record, "marketing_intelligence_keyword_updated")
        messages.success(request, "SEO opportunity updated.")
        return redirect(f"{reverse('marketing_intelligence')}#seo")
    return render(request, "marketing/intelligence_edit.html", {"title": "Edit SEO Opportunity", "form": form})


def edit_content(request, pk):
    if not _editor_required(request):
        return HttpResponseForbidden("Only Admin and Marketing Manager users can edit content ideas.")
    record = get_object_or_404(MarketingContentIdea, pk=pk)
    form = MarketingContentIdeaForm(request.POST or None, instance=record, assignee_choices=_assignee_choices())
    if request.method == "POST" and form.is_valid():
        record = form.save()
        _write_activity(request, record, "marketing_intelligence_content_updated")
        messages.success(request, "Content idea updated.")
        return redirect(f"{reverse('marketing_intelligence')}#content-planner")
    return render(request, "marketing/intelligence_edit.html", {"title": "Edit Content Idea", "form": form})


def marketing_report(request, report_type):
    try:
        report = build_marketing_report(report_type)
    except KeyError as exc:
        raise Http404("Unknown marketing report") from exc
    return render(request, "marketing/intelligence_report.html", {"report": report})
