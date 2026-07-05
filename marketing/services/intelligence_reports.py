from __future__ import annotations

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from marketing.models import MarketingCompetitor, MarketingContentIdea, MarketingKeywordPlan, MarketingVideoIdea


REPORT_LABELS = {
    "weekly": "Weekly Marketing Report",
    "monthly": "Monthly Marketing Report",
    "keywords": "Keyword Report",
    "content": "Content Report",
    "platforms": "Platform Report",
    "competitors": "Competitor Report",
}


def _content_rows(start=None, end=None):
    content = MarketingContentIdea.objects.select_related("assigned_to")
    videos = MarketingVideoIdea.objects.select_related("assigned_to")
    if start and end:
        content = content.filter(due_date__range=(start, end))
        videos = videos.filter(due_date__range=(start, end))
    rows = [
        {
            "title": item.title,
            "type": item.get_content_type_display(),
            "platform": item.get_target_platform_display(),
            "status": item.get_status_display(),
            "due": item.due_date,
            "owner": item.assigned_to,
        }
        for item in content[:100]
    ]
    rows.extend(
        {
            "title": item.video_title,
            "type": "Video",
            "platform": item.get_platform_display(),
            "status": item.get_status_display(),
            "due": item.due_date,
            "owner": item.assigned_to,
        }
        for item in videos[:100]
    )
    rows.sort(key=lambda row: (row["due"] is None, row["due"] or timezone.localdate(), row["title"].casefold()))
    return rows


def build_marketing_report(report_type: str) -> dict:
    if report_type not in REPORT_LABELS:
        raise KeyError(report_type)
    today = timezone.localdate()
    if report_type == "weekly":
        rows = _content_rows(today, today + timedelta(days=7))
    elif report_type == "monthly":
        month_start = today.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = _content_rows(month_start, next_month - timedelta(days=1))
    elif report_type == "keywords":
        rows = [
            {
                "title": item.keyword,
                "type": item.get_product_category_display(),
                "platform": item.get_target_country_display(),
                "status": item.get_status_display(),
                "due": None,
                "owner": item.created_by,
            }
            for item in MarketingKeywordPlan.objects.select_related("created_by")[:100]
        ]
    elif report_type == "content":
        rows = _content_rows()
    elif report_type == "platforms":
        content_counts = MarketingContentIdea.objects.values("target_platform").annotate(total=Count("id")).order_by("target_platform")
        video_counts = MarketingVideoIdea.objects.values("platform").annotate(total=Count("id")).order_by("platform")
        rows = [
            {"title": row["target_platform"].replace("_", " ").title(), "type": "Content", "platform": row["total"], "status": "Stored CRM data", "due": None, "owner": None}
            for row in content_counts
        ]
        rows.extend(
            {"title": row["platform"].replace("_", " ").title(), "type": "Video", "platform": row["total"], "status": "Stored CRM data", "due": None, "owner": None}
            for row in video_counts
        )
    else:
        rows = [
            {
                "title": item.name,
                "type": item.industry or item.category or "Competitor",
                "platform": item.get_country_display() or "—",
                "status": item.get_status_display(),
                "due": item.last_checked_at.date() if item.last_checked_at else None,
                "owner": None,
            }
            for item in MarketingCompetitor.objects.filter(is_active=True)[:100]
        ]
    return {"key": report_type, "title": REPORT_LABELS[report_type], "rows": rows, "generated_at": timezone.now()}
