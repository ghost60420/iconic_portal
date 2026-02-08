from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from marketing.ai.insights import generate_rule_based_insights as generate_legacy_insights
from marketing.models import (
    InsightItem,
    SocialMetricDaily,
    SocialContent,
    SocialAudienceDaily,
    AdMetricDaily,
    AdCampaign,
    SocialAccount,
)
from marketing.services.metrics import calc_engagement_total, calc_engagement_rate


def _upsert_insight(*, source: str, title: str, reason: str, action: str, priority: int = 50, platform: str = "", related_type: str = "", related_id: str = ""):
    InsightItem.objects.update_or_create(
        source=source,
        title=title,
        platform=platform or "",
        related_object_type=related_type or "",
        related_object_id=str(related_id) if related_id else "",
        defaults={
            "reason": reason,
            "recommended_action": action,
            "priority_score": priority,
            "status": "open",
        },
    )


def _top_key(d: dict) -> str:
    if not isinstance(d, dict) or not d:
        return ""
    return max(d.keys(), key=lambda k: d.get(k, 0))


def generate_content_insights(days: int = 30):
    since = timezone.localdate() - timedelta(days=days)

    rows = (
        SocialMetricDaily.objects.filter(date__gte=since)
        .values("content_id")
        .annotate(
            impressions=Sum("impressions"),
            reach=Sum("reach"),
            views=Sum("views"),
            clicks=Sum("clicks"),
            likes=Sum("likes"),
            comments=Sum("comments"),
            shares=Sum("shares"),
            saves=Sum("saves"),
        )
    )

    if not rows:
        return

    content_map = SocialContent.objects.in_bulk([r["content_id"] for r in rows])
    enriched = []
    for row in rows:
        content = content_map.get(row["content_id"])
        if not content:
            continue
        engagement_total = calc_engagement_total(
            likes=row.get("likes") or 0,
            comments=row.get("comments") or 0,
            shares=row.get("shares") or 0,
            saves=row.get("saves") or 0,
        )
        engagement_rate = calc_engagement_rate(
            impressions=row.get("impressions") or 0,
            reach=row.get("reach") or 0,
            views=row.get("views") or 0,
            engagement_total=engagement_total,
        )
        enriched.append(
            {
                "content": content,
                "impressions": row.get("impressions") or 0,
                "views": row.get("views") or 0,
                "engagement_total": engagement_total,
                "engagement_rate": engagement_rate,
            }
        )

    if not enriched:
        return

    rates = sorted([r["engagement_rate"] for r in enriched])
    impressions = sorted([r["impressions"] for r in enriched])
    top_quartile = rates[int(0.75 * (len(rates) - 1))] if rates else 0
    bottom_quartile = rates[int(0.25 * (len(rates) - 1))] if rates else 0
    median_impressions = impressions[len(impressions) // 2] if impressions else 0

    for row in enriched:
        content = row["content"]
        if row["engagement_rate"] >= top_quartile and row["impressions"] >= median_impressions:
            _upsert_insight(
                source="content",
                platform=content.platform,
                title=f"Winner content: {content.title or content.external_content_id}",
                reason=f"Engagement rate {row['engagement_rate']:.1%} with {row['impressions']} impressions",
                action="Repurpose this topic and maintain the same hook format.",
                priority=80,
                related_type="SocialContent",
                related_id=content.id,
            )
        elif row["engagement_rate"] <= bottom_quartile and row["impressions"] >= median_impressions:
            _upsert_insight(
                source="content",
                platform=content.platform,
                title=f"Weak content: {content.title or content.external_content_id}",
                reason=f"Low engagement rate {row['engagement_rate']:.1%} with {row['impressions']} impressions",
                action="Test a stronger hook or tighter caption for this topic.",
                priority=65,
                related_type="SocialContent",
                related_id=content.id,
            )

    format_scores = {}
    for row in enriched:
        content = row["content"]
        key = (content.platform, content.content_type)
        format_scores.setdefault(key, []).append(row["engagement_rate"])

    for (platform, content_type), rates in format_scores.items():
        if not rates:
            continue
        avg_rate = sum(rates) / max(len(rates), 1)
        if avg_rate >= top_quartile:
            _upsert_insight(
                source="content",
                platform=platform,
                title=f"Format winner: {content_type}",
                reason=f"Avg engagement rate {avg_rate:.1%} on {content_type}",
                action="Prioritize this format in the next posting cycle.",
                priority=70,
            )

    day_scores = {}
    hour_scores = {}
    for row in enriched:
        content = row["content"]
        if not content.published_at:
            continue
        day = content.published_at.strftime("%A")
        hour = content.published_at.strftime("%H:00")
        day_scores[day] = day_scores.get(day, 0) + row["engagement_total"]
        hour_scores[hour] = hour_scores.get(hour, 0) + row["engagement_total"]

    if day_scores:
        best_day = max(day_scores, key=day_scores.get)
        _upsert_insight(
            source="content",
            title="Best posting day detected",
            reason=f"Top engagement on {best_day}",
            action="Schedule priority posts on this day.",
            priority=60,
        )

    if hour_scores:
        best_hour = max(hour_scores, key=hour_scores.get)
        _upsert_insight(
            source="content",
            title="Best posting hour detected",
            reason=f"Top engagement at {best_hour}",
            action="Post during this hour window for higher engagement.",
            priority=60,
        )


def generate_audience_insights():
    latest = {}
    for row in SocialAudienceDaily.objects.select_related("account").order_by("-date"):
        if row.account_id not in latest:
            latest[row.account_id] = row

    for row in latest.values():
        top_country = _top_key(row.country_json)
        top_age = _top_key(row.gender_age_json)
        if top_country:
            _upsert_insight(
                source="audience",
                platform=row.account.platform,
                title=f"Top audience country: {top_country}",
                reason="Largest share of audience",
                action="Tailor offers and visuals to this location.",
                priority=55,
            )
        if top_age:
            _upsert_insight(
                source="audience",
                platform=row.account.platform,
                title=f"Top audience age group: {top_age}",
                reason="Highest audience concentration",
                action="Adjust creative tone for this segment.",
                priority=55,
            )


def generate_ads_insights(days: int = 30):
    since = timezone.localdate() - timedelta(days=days)

    rows = (
        AdMetricDaily.objects.filter(date__gte=since)
        .values("ad_campaign_id")
        .annotate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            conversions=Sum("conversions"),
        )
    )

    campaign_map = AdCampaign.objects.in_bulk([r["ad_campaign_id"] for r in rows])
    for row in rows:
        campaign = campaign_map.get(row["ad_campaign_id"])
        if not campaign:
            continue
        spend = float(row.get("spend") or 0)
        conversions = int(row.get("conversions") or 0)
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        ctr = (clicks / impressions) if impressions else 0

        if spend >= 100 and conversions == 0:
            _upsert_insight(
                source="ads",
                title=f"High spend, no conversions: {campaign.name or campaign.external_campaign_id}",
                reason=f"Spend {spend:.2f} with 0 conversions",
                action="Review offer and landing page alignment.",
                priority=85,
                related_type="AdCampaign",
                related_id=campaign.id,
            )
        elif ctr >= 0.02 and conversions == 0:
            _upsert_insight(
                source="ads",
                title=f"Strong CTR, low conversions: {campaign.name or campaign.external_campaign_id}",
                reason=f"CTR {ctr:.1%} but no conversions",
                action="Tighten landing page message and pricing clarity.",
                priority=75,
                related_type="AdCampaign",
                related_id=campaign.id,
            )


def generate_llm_insights():
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return
    if not getattr(settings, "MARKETING_AI_ENABLED", False):
        return

    prompt = (
        "Create a weekly marketing action plan for a fashion manufacturing brand. "
        "Include 10 content topics, 5 hooks for each, and short caption ideas per platform."
    )

    try:
        from crm.ai_client import get_openai_client

        client = get_openai_client()
        response = client.chat.completions.create(
            model=getattr(settings, "OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        content = response.choices[0].message.content.strip()
    except Exception:
        return

    if content:
        _upsert_insight(
            source="content",
            title="Weekly AI content plan",
            reason="Generated by LLM",
            action=content,
            priority=60,
        )


def generate_insights(days: int = 30):
    generate_legacy_insights()
    generate_content_insights(days=days)
    generate_audience_insights()
    if getattr(settings, "MARKETING_ADS_ENABLED", False):
        generate_ads_insights(days=days)
    generate_llm_insights()
