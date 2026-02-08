from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from marketing.models import InsightItem, SeoQueryDaily, SocialMetricDaily, OutreachSendLog


def _upsert_insight(*, source: str, title: str, reason: str, action: str, priority: int = 50):
    InsightItem.objects.update_or_create(
        source=source,
        title=title,
        defaults={
            "reason": reason,
            "recommended_action": action,
            "priority_score": priority,
            "status": "open",
        },
    )


def generate_rule_based_insights(days: int = 14):
    since = timezone.localdate() - timedelta(days=days)

    # SEO: high impressions, low ctr
    seo_rows = (
        SeoQueryDaily.objects.filter(date__gte=since)
        .values("query")
        .annotate(impressions=Sum("impressions"), clicks=Sum("clicks"))
        .order_by("-impressions")[:10]
    )

    for row in seo_rows:
        impressions = row.get("impressions") or 0
        clicks = row.get("clicks") or 0
        if impressions >= 500 and impressions > 0:
            ctr = (clicks / impressions) if impressions else 0
            if ctr < 0.02:
                _upsert_insight(
                    source="seo",
                    title=f"Low CTR on query: {row.get('query')}",
                    reason=f"{impressions} impressions with {clicks} clicks",
                    action="Update page title/meta and add stronger CTA.",
                    priority=80,
                )

    # Social: top engagement rate
    social_rows = (
        SocialMetricDaily.objects.filter(date__gte=since)
        .values("content_id")
        .annotate(
            impressions=Sum("impressions"),
            likes=Sum("likes"),
            comments=Sum("comments"),
            shares=Sum("shares"),
            saves=Sum("saves"),
        )
    )
    for row in social_rows:
        impressions = row.get("impressions") or 0
        if impressions == 0:
            continue
        engagement = (row.get("likes") or 0) + (row.get("comments") or 0) + (row.get("shares") or 0) + (row.get("saves") or 0)
        rate = engagement / impressions
        if rate >= 0.05:
            _upsert_insight(
                source="social",
                title="High engagement content detected",
                reason=f"Engagement rate {rate:.1%} on recent post",
                action="Repurpose this format and post at similar time.",
                priority=70,
            )
            break

    # Outreach: low reply rate
    sent = OutreachSendLog.objects.filter(status="sent", sent_at__date__gte=since).count()
    replied = OutreachSendLog.objects.filter(status="replied", sent_at__date__gte=since).count()
    if sent >= 50:
        reply_rate = replied / max(sent, 1)
        if reply_rate < 0.02:
            _upsert_insight(
                source="outreach",
                title="Low reply rate on outreach",
                reason=f"Reply rate {reply_rate:.1%} on {sent} sends",
                action="Test 2 new subject lines and add personalization.",
                priority=85,
            )


def generate_llm_insights(prompt: str) -> str:
    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return ""
    try:
        from crm.ai_client import get_openai_client
        client = get_openai_client()
        response = client.chat.completions.create(
            model=getattr(settings, "OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return ""
