from marketing.models import (
    SeoQueryDaily,
    SeoPageDaily,
    SocialMetricDaily,
    SocialAudienceDaily,
    AccountMetricDaily,
    AdMetricDaily,
)


def upsert_seo_query_daily(*, property_obj, payload: dict):
    return SeoQueryDaily.objects.update_or_create(
        property=property_obj,
        date=payload.get("date"),
        query=payload.get("query", ""),
        page=payload.get("page", ""),
        country=payload.get("country", ""),
        device=payload.get("device", ""),
        defaults={
            "clicks": payload.get("clicks", 0),
            "impressions": payload.get("impressions", 0),
            "ctr": payload.get("ctr", 0) or 0,
            "position": payload.get("position", 0) or 0,
        },
    )


def upsert_seo_page_daily(*, property_obj, payload: dict):
    return SeoPageDaily.objects.update_or_create(
        property=property_obj,
        date=payload.get("date"),
        page=payload.get("page", ""),
        defaults={
            "clicks": payload.get("clicks", 0),
            "impressions": payload.get("impressions", 0),
            "ctr": payload.get("ctr", 0) or 0,
            "position": payload.get("position", 0) or 0,
        },
    )


def upsert_social_metric_daily(*, content_obj, payload: dict):
    return SocialMetricDaily.objects.update_or_create(
        content=content_obj,
        date=payload.get("date"),
        defaults={
            "impressions": payload.get("impressions", 0),
            "reach": payload.get("reach", 0),
            "views": payload.get("views", 0),
            "likes": payload.get("likes", 0),
            "comments": payload.get("comments", 0),
            "shares": payload.get("shares", 0),
            "saves": payload.get("saves", 0),
            "clicks": payload.get("clicks", 0),
            "watch_time_seconds": payload.get("watch_time_seconds", 0),
            "avg_view_duration_seconds": payload.get("avg_view_duration_seconds", 0),
            "profile_visits": payload.get("profile_visits", 0),
            "follows": payload.get("follows", 0),
        },
    )


def upsert_social_audience_daily(*, account_obj, payload: dict):
    return SocialAudienceDaily.objects.update_or_create(
        account=account_obj,
        date=payload.get("date"),
        defaults={
            "country_json": payload.get("country_json", {}) or {},
            "city_json": payload.get("city_json", {}) or {},
            "gender_age_json": payload.get("gender_age_json", {}) or {},
            "language_json": payload.get("language_json", {}) or {},
        },
    )


def upsert_account_metric_daily(*, account_obj, payload: dict):
    return AccountMetricDaily.objects.update_or_create(
        account=account_obj,
        date=payload.get("date"),
        defaults={
            "followers_total": payload.get("followers_total", 0),
            "followers_change": payload.get("followers_change", 0),
            "impressions": payload.get("impressions", 0),
            "reach": payload.get("reach", 0),
            "views": payload.get("views", 0),
            "clicks": payload.get("clicks", 0),
            "engagement_total": payload.get("engagement_total", 0),
        },
    )


def upsert_ad_metric_daily(*, ad_campaign_obj, payload: dict):
    return AdMetricDaily.objects.update_or_create(
        ad_campaign=ad_campaign_obj,
        date=payload.get("date"),
        defaults={
            "spend": payload.get("spend", 0) or 0,
            "impressions": payload.get("impressions", 0),
            "clicks": payload.get("clicks", 0),
            "cpc": payload.get("cpc", 0) or 0,
            "cpm": payload.get("cpm", 0) or 0,
            "conversions": payload.get("conversions", 0),
            "cost_per_conversion": payload.get("cost_per_conversion", 0) or 0,
        },
    )
