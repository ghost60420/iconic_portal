from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from marketing.ai.insights import generate_rule_based_insights as generate_legacy_insights
from marketing.models import (
    InsightItem,
    AccountMetricDaily,
    SocialMetricDaily,
    SocialContent,
    SocialAudienceDaily,
    AdMetricDaily,
    AdCampaign,
    SocialAccount,
)
from marketing.services.metrics import calc_engagement_total, calc_engagement_rate, calc_engagement_score


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


def _content_metric_rollup(days: int = 30):
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
        return []

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
        engagement_score = calc_engagement_score(
            likes=row.get("likes") or 0,
            comments=row.get("comments") or 0,
            shares=row.get("shares") or 0,
            saves=row.get("saves") or 0,
            clicks=row.get("clicks") or 0,
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
                "reach": row.get("reach") or 0,
                "impressions": row.get("impressions") or 0,
                "views": row.get("views") or 0,
                "clicks": row.get("clicks") or 0,
                "likes": row.get("likes") or 0,
                "comments": row.get("comments") or 0,
                "shares": row.get("shares") or 0,
                "saves": row.get("saves") or 0,
                "engagement_total": engagement_total,
                "engagement_score": engagement_score,
                "engagement_rate": engagement_rate,
            }
        )
    return enriched


def generate_platform_insights(days: int = 30):
    since = timezone.localdate() - timedelta(days=days)

    rows = (
        SocialMetricDaily.objects.filter(date__gte=since)
        .values("content__platform")
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

    platform_stats = []
    for row in rows:
        platform = row.get("content__platform") or ""
        if not platform:
            continue
        engagement_total = calc_engagement_total(
            likes=row.get("likes") or 0,
            comments=row.get("comments") or 0,
            shares=row.get("shares") or 0,
            saves=row.get("saves") or 0,
        )
        engagement_score = calc_engagement_score(
            likes=row.get("likes") or 0,
            comments=row.get("comments") or 0,
            shares=row.get("shares") or 0,
            saves=row.get("saves") or 0,
            clicks=row.get("clicks") or 0,
        )
        engagement_rate = calc_engagement_rate(
            impressions=row.get("impressions") or 0,
            reach=row.get("reach") or 0,
            views=row.get("views") or 0,
            engagement_total=engagement_total,
        )
        platform_stats.append(
            {
                "platform": platform,
                "impressions": row.get("impressions") or 0,
                "reach": row.get("reach") or 0,
                "views": row.get("views") or 0,
                "clicks": row.get("clicks") or 0,
                "engagement_total": engagement_total,
                "engagement_score": engagement_score,
                "engagement_rate": engagement_rate,
            }
        )

    if not platform_stats:
        return

    average_rate = sum(item["engagement_rate"] for item in platform_stats) / max(len(platform_stats), 1)
    best_platform = max(platform_stats, key=lambda item: (item["engagement_rate"], item["engagement_score"], item["clicks"]))
    weak_platform = min(platform_stats, key=lambda item: (item["engagement_rate"], item["engagement_score"]))
    low_click_platform = min(
        platform_stats,
        key=lambda item: ((item["clicks"] / max(item["reach"] or item["views"] or 1, 1)) if (item["reach"] or item["views"]) else 0),
    )

    _upsert_insight(
        source="social",
        platform=best_platform["platform"],
        title=f"Best platform: {dict(SocialAccount.PLATFORM_CHOICES).get(best_platform['platform'], best_platform['platform'])}",
        reason=f"Engagement rate reached {best_platform['engagement_rate']:.1%}, above the average {average_rate:.1%}.",
        action="Double down on this platform with your best recent content format.",
        priority=88,
        related_type="best_platform",
        related_id=best_platform["platform"],
    )

    _upsert_insight(
        source="social",
        platform=weak_platform["platform"],
        title=f"Weak platform: {dict(SocialAccount.PLATFORM_CHOICES).get(weak_platform['platform'], weak_platform['platform'])}",
        reason=f"Engagement rate is only {weak_platform['engagement_rate']:.1%}, below your current platform average.",
        action="Refresh hooks and CTA structure on this platform first.",
        priority=78,
        related_type="weak_platform",
        related_id=weak_platform["platform"],
    )

    low_click_base = max(low_click_platform["reach"] or low_click_platform["views"], 1)
    low_click_rate = low_click_platform["clicks"] / low_click_base
    if low_click_base >= 50 and low_click_rate < 0.02:
        _upsert_insight(
            source="social",
            platform=low_click_platform["platform"],
            title="Low click warning",
            reason=f"{dict(SocialAccount.PLATFORM_CHOICES).get(low_click_platform['platform'], low_click_platform['platform'])} is converting attention into clicks at only {low_click_rate:.1%}.",
            action="Use a clearer offer and stronger CTA on click-focused posts.",
            priority=72,
            related_type="low_click_warning",
            related_id=low_click_platform["platform"],
        )


def generate_content_insights(days: int = 30):
    enriched = _content_metric_rollup(days=days)
    if not enriched:
        return

    rates = sorted([r["engagement_rate"] for r in enriched])
    impressions = sorted([r["impressions"] for r in enriched])
    top_quartile = rates[int(0.75 * (len(rates) - 1))] if rates else 0
    bottom_quartile = rates[int(0.25 * (len(rates) - 1))] if rates else 0
    median_impressions = impressions[len(impressions) // 2] if impressions else 0

    strong_rows = [
        row
        for row in enriched
        if row["engagement_rate"] >= top_quartile and row["impressions"] >= median_impressions
    ]
    if strong_rows:
        best_row = max(strong_rows, key=lambda item: (item["engagement_score"], item["engagement_rate"], item["clicks"]))
        content = best_row["content"]
        _upsert_insight(
            source="content",
            platform=content.platform,
            title=f"High engagement content: {content.title or content.external_content_id}",
            reason=f"This post reached {best_row['engagement_rate']:.1%} engagement with score {best_row['engagement_score']}.",
            action="Reuse this topic and creative structure in the next content batch.",
            priority=84,
            related_type="high_engagement_content",
            related_id=content.id,
        )

    format_scores = {}
    for row in enriched:
        content = row["content"]
        key = content.content_type
        format_scores.setdefault(key, []).append(row["engagement_rate"])

    if format_scores:
        best_content_type, content_type_rates = max(
            format_scores.items(),
            key=lambda item: sum(item[1]) / max(len(item[1]), 1),
        )
        avg_rate = sum(content_type_rates) / max(len(content_type_rates), 1)
        _upsert_insight(
            source="content",
            title=f"Best content type: {best_content_type.replace('_', ' ').title()}",
            reason=f"This format is averaging {avg_rate:.1%} engagement across recent posts.",
            action="Prioritize more of this content type next week.",
            priority=76,
            related_type="best_content_type",
            related_id=best_content_type,
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
            title=f"Best posting day: {best_day}",
            reason=f"Recent content produced the strongest engagement on {best_day}.",
            action="Schedule one of next week's strongest posts on this day.",
            priority=68,
            related_type="best_posting_day",
            related_id=best_day,
        )

    if hour_scores:
        best_hour = max(hour_scores, key=hour_scores.get)
        _upsert_insight(
            source="content",
            title=f"Best posting hour: {best_hour}",
            reason=f"Recent posts drew their strongest engagement around {best_hour}.",
            action="Test your next post in this hour window.",
            priority=66,
            related_type="best_posting_hour",
            related_id=best_hour,
        )


def generate_audience_insights(days: int = 30):
    since = timezone.localdate() - timedelta(days=days)

    follower_rows = (
        AccountMetricDaily.objects.filter(date__gte=since)
        .values("account__platform")
        .annotate(follower_change=Sum("followers_change"))
    )
    for row in follower_rows:
        follower_change = row.get("follower_change") or 0
        platform = row.get("account__platform") or ""
        if platform and follower_change < 0:
            _upsert_insight(
                source="audience",
                platform=platform,
                title=f"Audience growth warning: {dict(SocialAccount.PLATFORM_CHOICES).get(platform, platform)}",
                reason=f"Follower change is negative at {follower_change} during the current period.",
                action="Post more consistently and review which content type is losing attention.",
                priority=74,
                related_type="audience_growth_warning",
                related_id=platform,
            )

    latest = {}
    for row in SocialAudienceDaily.objects.select_related("account").order_by("-date"):
        if row.account_id not in latest:
            latest[row.account_id] = row

    for row in latest.values():
        top_country = _top_key(row.country_json)
        if top_country:
            _upsert_insight(
                source="audience",
                platform=row.account.platform,
                title=f"Top audience country: {top_country}",
                reason="This location currently holds the largest audience share.",
                action="Tailor one post or offer to this location.",
                priority=52,
                related_type="audience_location",
                related_id=top_country,
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
    generate_legacy_insights(days=max(min(days, 30), 7))
    generate_platform_insights(days=days)
    generate_content_insights(days=days)
    generate_audience_insights(days=days)
    if getattr(settings, "MARKETING_ADS_ENABLED", False):
        generate_ads_insights(days=days)
    generate_llm_insights()
