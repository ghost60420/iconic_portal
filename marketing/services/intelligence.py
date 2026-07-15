from __future__ import annotations

import re
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from marketing.models import (
    AccountMetricDaily,
    MarketingContentIdea,
    MarketingKeywordPlan,
    MarketingVideoIdea,
    SeoPageDaily,
    SeoQueryDaily,
    SocialAccount,
    SocialContent,
    SocialMetricDaily,
    WebsitePageDaily,
    WebsiteTrafficDaily,
)

from marketing.services.oauth_connections import (
    LINKEDIN_REQUIRED_ORGANIZATION_SCOPES,
    oauth_scope_set,
)


def linkedin_connection_status(credential) -> dict:
    if not credential or not credential.is_active or not credential.has_access_token:
        return {
            "key": "linkedin",
            "label": "LinkedIn",
            "status": "disconnected",
            "status_label": "Disconnected",
            "tone": "warn",
            "message": "Connect a LinkedIn account to begin organization reporting.",
            "approval_required": False,
        }

    granted = oauth_scope_set(credential.scopes)
    missing = sorted(LINKEDIN_REQUIRED_ORGANIZATION_SCOPES - granted)
    if missing:
        return {
            "key": "linkedin",
            "label": "LinkedIn",
            "status": "approval_required",
            "status_label": "LinkedIn API approval required",
            "tone": "warn",
            "message": (
                "The personal OAuth token is valid, but LinkedIn organization access is unavailable. "
                "Enable the Community Management API product and approve the organization scopes: "
                f"{', '.join(missing)}."
            ),
            "approval_required": True,
            "missing_scopes": missing,
        }

    if credential.expires_at and credential.expires_at <= timezone.now():
        return {
            "key": "linkedin",
            "label": "LinkedIn",
            "status": "expired",
            "status_label": "Reconnect required",
            "tone": "warn",
            "message": "The LinkedIn token is expired. Reconnect LinkedIn.",
            "approval_required": False,
        }

    if credential.last_error:
        return {
            "key": "linkedin",
            "label": "LinkedIn",
            "status": "error",
            "status_label": "API error",
            "tone": "warn",
            "message": credential.last_error,
            "approval_required": False,
        }

    return {
        "key": "linkedin",
        "label": "LinkedIn",
        "status": "connected",
        "status_label": "Connected",
        "tone": "good",
        "message": "LinkedIn organization access is ready.",
        "approval_required": False,
    }


def _credential_status(key: str, label: str, credential, *, social_count: int = 0) -> dict:
    if not credential or not credential.is_active or not credential.has_access_token:
        return {
            "key": key,
            "label": label,
            "status_label": "Disconnected",
            "tone": "warn",
            "message": "No active OAuth connection.",
        }
    if credential.expires_at and credential.expires_at <= timezone.now() and not credential.has_refresh_token:
        return {
            "key": key,
            "label": label,
            "status_label": "Reconnect required",
            "tone": "warn",
            "message": "The access token is expired and no refresh token is available.",
        }
    if credential.last_error:
        error = credential.last_error
        if key == "google_business" and ("quota" in error.lower() or "resource_exhausted" in error.lower()):
            return {
                "key": key,
                "label": label,
                "status_label": "API approval pending",
                "tone": "warn",
                "message": "Google Business Profile API access or quota is pending. Planning tools remain available.",
            }
        return {
            "key": key,
            "label": label,
            "status_label": "Needs attention",
            "tone": "warn",
            "message": error,
        }
    return {
        "key": key,
        "label": label,
        "status_label": "Connected",
        "tone": "good",
        "message": f"Stored data source ready{f' ({social_count} account(s))' if social_count else ''}.",
    }


def build_data_source_status(*, credentials_by_platform: dict, social_counts: dict, website_rows: int) -> list[dict]:
    google = credentials_by_platform.get("google")
    rows = [
        _credential_status("gsc", "Google Search Console", google),
        _credential_status("google_business", "Google Business Profile", google, social_count=social_counts.get("google_business", 0)),
        {
            "key": "website",
            "label": "Website Analytics",
            "status_label": "Stored data ready" if website_rows else "No stored data",
            "tone": "good" if website_rows else "neutral",
            "message": f"{website_rows} stored daily analytics row(s)." if website_rows else "Connect or sync website analytics when available.",
        },
        _credential_status("facebook", "Facebook", credentials_by_platform.get("facebook") or credentials_by_platform.get("meta"), social_count=social_counts.get("facebook", 0)),
        _credential_status("instagram", "Instagram", credentials_by_platform.get("instagram"), social_count=social_counts.get("instagram", 0)),
        linkedin_connection_status(credentials_by_platform.get("linkedin")),
        _credential_status("tiktok", "TikTok", credentials_by_platform.get("tiktok"), social_count=social_counts.get("tiktok", 0)),
    ]
    return rows


def build_internal_recommendations(*, keywords, content_ideas, video_ideas, competitors) -> list[dict]:
    priority_order = {"high": 0, "medium": 1, "low": 2}
    ordered_keywords = sorted(keywords, key=lambda item: (priority_order.get(item.priority, 9), item.keyword.lower()))
    primary = ordered_keywords[0] if ordered_keywords else None
    keyword = primary.keyword if primary else "private label clothing manufacturer"
    category = primary.get_product_category_display() if primary else "Private label apparel"
    audience = primary.target_audience if primary and primary.target_audience else "startup clothing brands"
    competitor_note = (
        f"Review {competitors[0].name} before publishing."
        if competitors
        else "Add competitors to validate positioning before publishing."
    )
    existing_titles = {item.title.casefold() for item in content_ideas}
    existing_videos = {item.video_title.casefold() for item in video_ideas}

    suggestions = [
        {
            "type": "Keyword idea",
            "title": keyword,
            "reason": f"Prioritize this {category.lower()} topic for {audience}.",
        },
        {
            "type": "Blog idea",
            "title": f"Buyer guide: choosing a {category.lower()} partner",
            "reason": competitor_note,
        },
        {
            "type": "Video idea",
            "title": f"How {category.lower()} production works",
            "reason": "Use stored keyword priorities to structure the hook and talking points.",
        },
        {
            "type": "Google Business post idea",
            "title": f"Inside our {category.lower()} process",
            "reason": "Prepare the copy now; publishing remains manual while API approval is pending.",
        },
        {
            "type": "LinkedIn post idea",
            "title": f"What brands should know about {keyword}",
            "reason": "Draft internally; LinkedIn organization publishing remains disabled pending API approval.",
        },
        {
            "type": "Instagram reel idea",
            "title": f"Three details that define quality {category.lower()}",
            "reason": "Turn the highest-priority product topic into a short educational reel.",
        },
    ]
    for suggestion in suggestions:
        title_key = suggestion["title"].casefold()
        suggestion["already_planned"] = title_key in existing_titles or title_key in existing_videos
    return suggestions


def _clean_phrase(value: str) -> str:
    return " ".join((value or "").strip().split())


def generate_keyword_recommendations(*, country: str, industry: str, product: str, target_customer: str) -> dict:
    """Generate deterministic, internal-only keyword groups. No external AI or customer data is used."""
    industry = _clean_phrase(industry).lower()
    product = _clean_phrase(product).lower()
    customer = _clean_phrase(target_customer).lower()
    country_label = dict(MarketingKeywordPlan._meta.get_field("target_country").choices).get(country, country)
    country_text = country_label.lower()
    product_root = re.sub(r"\s+", " ", product) or "apparel manufacturing"
    industry_root = re.sub(r"\s+", " ", industry) or "apparel manufacturing"
    customer_root = customer or "clothing brands"
    return {
        "primary_keywords": [product_root, f"{product_root} {country_text}", industry_root],
        "secondary_keywords": [f"custom {product_root}", f"private label {product_root}", f"{industry_root} supplier"],
        "long_tail_keywords": [
            f"best {product_root} for {customer_root}",
            f"how to choose a {product_root} partner in {country_text}",
            f"low MOQ {product_root} for {customer_root}",
        ],
        "customer_questions": [
            f"How much does {product_root} cost?",
            f"What is the minimum order for {product_root}?",
            f"How long does {product_root} production take?",
        ],
        "comparison_keywords": [f"{product_root} vs overseas supplier", f"{country_text} vs Bangladesh {industry_root}"],
        "buying_intent_keywords": [f"hire {product_root} supplier", f"{product_root} quote", f"order {product_root}"],
        "commercial_keywords": [f"best {product_root} company", f"{product_root} pricing", f"trusted {industry_root}"],
        "local_keywords": [f"{product_root} {country_text}", f"{industry_root} near me"],
        "brand_keywords": [f"Iconic Apparel {product_root}", f"Iconic Apparel {industry_root}"],
        "blog_ideas": [
            f"Complete buyer guide to {product_root}",
            f"How {customer_root} can plan {product_root}",
            f"Common {product_root} sourcing mistakes",
        ],
        "video_ideas": [
            f"Inside the {product_root} production process",
            f"Three quality checks for {product_root}",
            f"From idea to finished {product_root}",
        ],
        "social_post_ideas": [
            f"One thing brands misunderstand about {product_root}",
            f"Behind the scenes: {product_root}",
            f"Quick answer: minimum order for {product_root}",
        ],
        "google_business_post_ideas": [f"Now planning {product_root} for {customer_root}", f"Our approach to quality {product_root}"],
        "email_campaign_ideas": [f"Your {product_root} production checklist", f"Plan your next {product_root} order"],
    }


def dashboard_snapshot(*, month_start, month_end) -> dict:
    """Return exact cross-table dashboard counts in one bounded SQL statement."""
    quote = connection.ops.quote_name
    keyword_table = quote(MarketingKeywordPlan._meta.db_table)
    content_table = quote(MarketingContentIdea._meta.db_table)
    video_table = quote(MarketingVideoIdea._meta.db_table)
    seo_table = quote(SeoQueryDaily._meta.db_table)
    seo_page_table = quote(SeoPageDaily._meta.db_table)
    website_traffic_table = quote(WebsiteTrafficDaily._meta.db_table)
    website_page_table = quote(WebsitePageDaily._meta.db_table)
    social_content_table = quote(SocialContent._meta.db_table)
    social_account_table = quote(SocialAccount._meta.db_table)
    social_metric_table = quote(SocialMetricDaily._meta.db_table)
    account_metric_table = quote(AccountMetricDaily._meta.db_table)
    live_social_filter = "('facebook', 'instagram', 'youtube')"
    sql = f"""
        SELECT
          (SELECT COUNT(*) FROM {keyword_table}),
          (SELECT COUNT(*) FROM {keyword_table} WHERE status = 'published'),
          (SELECT COUNT(*) FROM {keyword_table} WHERE landing_page_suggestion <> ''),
          (SELECT COUNT(*) FROM {content_table}),
          (SELECT COUNT(*) FROM {content_table} WHERE content_type = 'blog' AND status = 'published'),
          (SELECT COUNT(*) FROM {content_table} WHERE content_type = 'blog' AND status = 'scheduled'),
          (SELECT COUNT(*) FROM {video_table}),
          (SELECT COUNT(*) FROM {video_table} WHERE status = 'published'),
          (SELECT COUNT(*) FROM {video_table} WHERE status = 'scheduled'),
          (SELECT COUNT(*) FROM {content_table} WHERE target_platform = 'google_business'),
          (SELECT COUNT(*) FROM {content_table} WHERE target_platform = 'google_business' AND status = 'published'),
          (SELECT COUNT(*) FROM {content_table} WHERE target_platform = 'linkedin'),
          (SELECT COUNT(*) FROM {content_table} WHERE target_platform = 'instagram'),
          (SELECT COUNT(*) FROM {content_table} WHERE target_platform = 'tiktok'),
          (SELECT COUNT(*) FROM {content_table} WHERE target_platform = 'email'),
          ((SELECT COUNT(*) FROM {content_table} WHERE status = 'published' AND updated_at >= %s AND updated_at < %s)
           + (SELECT COUNT(*) FROM {video_table} WHERE status = 'published' AND updated_at >= %s AND updated_at < %s)),
          ((SELECT COUNT(*) FROM {content_table} WHERE due_date >= %s AND due_date < %s)
           + (SELECT COUNT(*) FROM {video_table} WHERE due_date >= %s AND due_date < %s)),
          ((SELECT COUNT(*) FROM {content_table} WHERE due_date >= %s AND due_date <= %s AND status NOT IN ('published', 'archived'))
           + (SELECT COUNT(*) FROM {video_table} WHERE due_date >= %s AND due_date <= %s AND status NOT IN ('published', 'archived'))),
          ((SELECT COUNT(*) FROM {content_table} WHERE due_date < %s AND status NOT IN ('published', 'archived'))
           + (SELECT COUNT(*) FROM {video_table} WHERE due_date < %s AND status NOT IN ('published', 'archived'))),
          (SELECT query FROM {seo_table} GROUP BY query ORDER BY SUM(clicks) DESC, SUM(impressions) DESC LIMIT 1),
          (SELECT COALESCE(SUM(visitors), 0) FROM {website_traffic_table} WHERE date >= %s AND date < %s),
          (SELECT COALESCE(SUM(visitors), 0) FROM {website_traffic_table} WHERE date >= %s AND date < %s),
          (SELECT COALESCE(SUM(sessions), 0) FROM {website_traffic_table} WHERE date >= %s AND date < %s),
          (SELECT COALESCE(SUM(engaged_sessions), 0) FROM {website_traffic_table} WHERE date >= %s AND date < %s),
          (SELECT COALESCE(SUM(conversions), 0) FROM {website_traffic_table} WHERE date >= %s AND date < %s),
          (SELECT COUNT(DISTINCT page_path) FROM {website_page_table} WHERE date >= %s AND date < %s AND visitors > 0),
          (SELECT COALESCE(SUM(clicks), 0) FROM {seo_table} WHERE date >= %s AND date < %s),
          (SELECT COALESCE(SUM(impressions), 0) FROM {seo_table} WHERE date >= %s AND date < %s),
          (SELECT COUNT(DISTINCT page) FROM {seo_page_table} WHERE date >= %s AND date < %s AND clicks > 0),
          (SELECT COUNT(*) FROM {social_content_table} WHERE platform IN {live_social_filter} AND published_at >= %s AND published_at < %s),
          (SELECT COUNT(*) FROM {social_content_table} WHERE platform IN {live_social_filter} AND published_at >= %s AND published_at < %s),
          (SELECT COUNT(DISTINCT DATE(published_at)) FROM {social_content_table} WHERE platform IN {live_social_filter} AND published_at >= %s AND published_at < %s),
          (SELECT COALESCE(SUM(m.impressions), 0) FROM {social_metric_table} m JOIN {social_content_table} c ON c.id = m.content_id WHERE c.platform IN {live_social_filter} AND m.date >= %s AND m.date < %s),
          (SELECT COALESCE(SUM(m.reach), 0) FROM {social_metric_table} m JOIN {social_content_table} c ON c.id = m.content_id WHERE c.platform IN {live_social_filter} AND m.date >= %s AND m.date < %s),
          (SELECT COALESCE(SUM(m.views), 0) FROM {social_metric_table} m JOIN {social_content_table} c ON c.id = m.content_id WHERE c.platform IN {live_social_filter} AND m.date >= %s AND m.date < %s),
          (SELECT COALESCE(SUM(m.likes + m.comments + m.shares + m.saves + m.clicks), 0) FROM {social_metric_table} m JOIN {social_content_table} c ON c.id = m.content_id WHERE c.platform IN {live_social_filter} AND m.date >= %s AND m.date < %s),
          (SELECT c.title FROM {social_content_table} c JOIN {social_metric_table} m ON m.content_id = c.id WHERE c.platform IN {live_social_filter} AND m.date >= %s AND m.date < %s GROUP BY c.id ORDER BY SUM(m.likes + m.comments + m.shares + m.saves + m.clicks + m.views) DESC LIMIT 1),
          (SELECT c.title FROM {social_content_table} c JOIN {social_metric_table} m ON m.content_id = c.id WHERE c.platform IN {live_social_filter} AND m.date >= %s AND m.date < %s AND (m.impressions + m.views + m.reach) > 0 GROUP BY c.id ORDER BY (SUM(m.likes + m.comments + m.shares + m.saves + m.clicks) * 1.0 / NULLIF(SUM(m.impressions + m.views + m.reach), 0)) ASC LIMIT 1),
          (SELECT COALESCE(SUM(m.impressions), 0) FROM {account_metric_table} m JOIN {social_account_table} a ON a.id = m.account_id WHERE a.platform = 'google_business' AND m.date >= %s AND m.date < %s),
          (SELECT COALESCE(SUM(m.reach), 0) FROM {account_metric_table} m JOIN {social_account_table} a ON a.id = m.account_id WHERE a.platform = 'google_business' AND m.date >= %s AND m.date < %s),
          (SELECT COALESCE(SUM(m.clicks), 0) FROM {account_metric_table} m JOIN {social_account_table} a ON a.id = m.account_id WHERE a.platform = 'google_business' AND m.date >= %s AND m.date < %s),
          (SELECT COALESCE(SUM(m.engagement_total), 0) FROM {account_metric_table} m JOIN {social_account_table} a ON a.id = m.account_id WHERE a.platform = 'google_business' AND m.date >= %s AND m.date < %s),
          (SELECT COUNT(*) FROM {account_metric_table} m JOIN {social_account_table} a ON a.id = m.account_id WHERE a.platform = 'google_business')
    """
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    previous_start = month_start - (month_end - month_start)
    previous_end = month_start
    params = [
        month_start, month_end, month_start, month_end,
        month_start.date(), month_end.date(), month_start.date(), month_end.date(),
        today, week_end, today, week_end, today, today,
        month_start.date(), month_end.date(),
        previous_start.date(), previous_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start, month_end,
        previous_start, previous_end,
        month_start, month_end,
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
        month_start.date(), month_end.date(),
    ]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    keys = [
        "seo_keywords", "published_keywords", "keywords_with_landing", "content_total",
        "published_blogs", "scheduled_blogs", "video_total", "published_videos", "scheduled_videos",
        "google_business_posts", "published_google_business_posts", "linkedin_posts", "instagram_posts",
        "tiktok_ideas", "email_campaigns", "completed_this_month", "planned_this_month",
        "due_this_week", "overdue", "top_keyword",
        "website_visitors_current", "website_visitors_previous", "website_sessions_current",
        "website_engaged_sessions_current", "website_conversions_current", "website_landing_pages_current",
        "gsc_clicks_current", "gsc_impressions_current", "gsc_pages_current",
        "social_posts_current", "social_posts_previous", "social_post_days_current",
        "social_impressions_current", "social_reach_current", "social_views_current", "social_engagement_current",
        "top_social_post", "low_social_post",
        "gbp_impressions_current", "gbp_reach_current", "gbp_clicks_current",
        "gbp_engagement_current", "gbp_metric_rows_total",
    ]
    snapshot = dict(zip(keys, row, strict=True))
    platform_counts = {
        "Google Business": snapshot["google_business_posts"],
        "LinkedIn": snapshot["linkedin_posts"],
        "Instagram": snapshot["instagram_posts"],
        "TikTok": snapshot["tiktok_ideas"],
        "Email": snapshot["email_campaigns"],
        "Video": snapshot["video_total"],
    }
    active_name, active_total = max(platform_counts.items(), key=lambda item: item[1])
    snapshot["most_active_platform"] = active_name if active_total else "No activity yet"
    snapshot["top_keyword"] = snapshot["top_keyword"] or "Waiting for performance data"
    return snapshot


def build_marketing_scores(snapshot: dict) -> list[dict]:
    def ratio(part, total):
        return min(100, round((part / total) * 100)) if total else 0

    def trend_score(current, previous):
        if not current and not previous:
            return None
        if not previous:
            return 70 if current else 0
        change = (current - previous) / max(previous, 1)
        return max(0, min(100, round(50 + (change * 100))))

    def score_row(label, score, basis, source_type="Live Data"):
        return {
            "label": label,
            "score": None if score is None else max(0, min(100, round(score))),
            "basis": basis,
            "source_type": source_type,
            "tone": "neutral" if score is None else "green" if score >= 70 else "yellow" if score >= 40 else "red",
        }

    social_distribution = ratio(snapshot["social_posts_current"], 12)
    content_volume = ratio(snapshot["social_posts_current"], 20)
    social_engagement_base = snapshot["social_impressions_current"] + snapshot["social_views_current"] + snapshot["social_reach_current"]
    social_engagement = ratio(snapshot["social_engagement_current"], social_engagement_base)
    schedule_adherence = ratio(snapshot["completed_this_month"], max(snapshot["planned_this_month"], 1))
    posting_consistency = ratio(snapshot["social_post_days_current"], 12)
    content_score = round((social_distribution * 0.25) + (social_engagement * 0.35) + (content_volume * 0.2) + (posting_consistency * 0.15) + (schedule_adherence * 0.05))
    if not snapshot["social_posts_current"] and not social_engagement_base:
        content_score = None

    website_trend = trend_score(snapshot["website_visitors_current"], snapshot["website_visitors_previous"])
    engagement_rate = ratio(snapshot["website_engaged_sessions_current"], snapshot["website_sessions_current"])
    conversion_score = min(100, snapshot["website_conversions_current"] * 20)
    landing_coverage = ratio(snapshot["website_landing_pages_current"] + snapshot["keywords_with_landing"], max(snapshot["seo_keywords"], 1))
    if website_trend is None:
        website_score = None
    else:
        website_score = round((website_trend * 0.35) + (conversion_score * 0.25) + (engagement_rate * 0.25) + (landing_coverage * 0.15))

    gbp_activity_base = snapshot["gbp_impressions_current"] + snapshot["gbp_reach_current"]
    if not snapshot["gbp_metric_rows_total"]:
        google_business_score = None
    else:
        gbp_engagement = ratio(snapshot["gbp_clicks_current"] + snapshot["gbp_engagement_current"], gbp_activity_base)
        gbp_volume = min(100, ratio(snapshot["gbp_metric_rows_total"], 30) + ratio(snapshot["gbp_impressions_current"], 1000) // 2)
        google_business_score = round((gbp_volume * 0.6) + (gbp_engagement * 0.4))

    if not social_engagement_base and not snapshot["social_posts_current"]:
        social_score = None
    else:
        social_trend = trend_score(snapshot["social_posts_current"], snapshot["social_posts_previous"]) or 0
        social_score = round((social_engagement * 0.45) + (social_distribution * 0.3) + (social_trend * 0.25))

    consistency_score = round((schedule_adherence * 0.45) + (posting_consistency * 0.45) - min(20, snapshot["overdue"] * 5))
    if not snapshot["planned_this_month"] and not snapshot["social_posts_current"]:
        consistency_score = None

    seo_score = None
    if snapshot["gsc_impressions_current"] or snapshot["gsc_clicks_current"] or snapshot["seo_keywords"]:
        gsc_click_rate = ratio(snapshot["gsc_clicks_current"], snapshot["gsc_impressions_current"])
        seo_score = round((gsc_click_rate * 0.45) + (landing_coverage * 0.35) + (ratio(snapshot["gsc_pages_current"], max(snapshot["seo_keywords"], 1)) * 0.2))

    values = [
        score_row("SEO Score", seo_score, f"{snapshot['gsc_clicks_current']} GSC clicks, {snapshot['gsc_impressions_current']} impressions, {snapshot['gsc_pages_current']} landing pages.", "Live Data" if seo_score is not None else "Unavailable Data"),
        score_row("Content Score", content_score, f"{snapshot['social_posts_current']} synced posts, {snapshot['social_engagement_current']} engagements, {snapshot['social_post_days_current']} posting days.", "Live Data" if content_score is not None else "Unavailable Data"),
        score_row("Social Score", social_score, f"Facebook, Instagram, and YouTube produced {snapshot['social_impressions_current'] + snapshot['social_views_current']} impressions/views.", "Live Data" if social_score is not None else "Unavailable Data"),
        score_row("Consistency Score", consistency_score, f"{snapshot['completed_this_month']} completed, {snapshot['planned_this_month']} planned, {snapshot['overdue']} overdue.", "Partial Data" if consistency_score is not None else "Unavailable Data"),
        score_row("Website Score", website_score, f"{snapshot['website_visitors_current']} visitors, {snapshot['website_conversions_current']} conversions, {engagement_rate}% engagement.", "Live Data" if website_score is not None else "Unavailable Data"),
        score_row("Google Business Score", google_business_score, f"{snapshot['gbp_metric_rows_total']} analytics rows, {snapshot['gbp_impressions_current']} impressions, {snapshot['gbp_clicks_current']} clicks.", "Partial Data" if google_business_score is not None else "Unavailable Data"),
    ]
    weights = {
        "SEO Score": 0.15,
        "Content Score": 0.18,
        "Social Score": 0.18,
        "Consistency Score": 0.14,
        "Website Score": 0.2,
        "Google Business Score": 0.15,
    }
    available = [item for item in values if item["score"] is not None]
    if available:
        total_weight = sum(weights[item["label"]] for item in available)
        overall = round(sum(item["score"] * weights[item["label"]] for item in available) / total_weight)
        values.append(score_row("Overall Marketing Health", overall, "Weighted average of available live and partial marketing metrics.", "Live Data" if len(available) >= 4 else "Partial Data"))
    else:
        values.append(score_row("Overall Marketing Health", None, "No synced marketing metrics available yet.", "Unavailable Data"))
    return values


def build_assistant_answers(*, keywords, content_ideas, video_ideas, summary: dict | None = None) -> list[dict]:
    summary = summary or {}
    keyword_list = list(keywords)
    content_list = list(content_ideas)
    video_list = list(video_ideas)
    article_keywords = {item.keyword.casefold() for item in content_list if item.content_type == "blog" and item.keyword}
    video_keywords = {item.target_keyword.casefold() for item in video_list if item.target_keyword}
    priority = sorted(keyword_list, key=lambda item: ({"high": 0, "medium": 1, "low": 2}.get(item.priority, 9), item.keyword.casefold()))
    target = priority[0] if priority else None
    no_article = next((item for item in priority if item.keyword.casefold() not in article_keywords), None)
    no_video = next((item for item in priority if item.keyword.casefold() not in video_keywords), None)
    no_landing = next((item for item in priority if not item.landing_page_suggestion), None)
    next_content = next((item for item in content_list if item.status not in {"published", "archived"}), None)
    website_current = summary.get("website_visitors_current", 0)
    website_previous = summary.get("website_visitors_previous", 0)
    website_direction = "up" if website_current >= website_previous else "down"
    top_post = summary.get("top_social_post") or "No top post available yet"
    weak_post = summary.get("low_social_post") or "No low-performing post available yet"
    website_answer = (
        f"GA4 visitors are {website_direction}: {website_current} current vs {website_previous} previous."
        if website_current or website_previous
        else "No GA4 visitor data available for the selected period."
    )
    top_keyword = summary.get("top_keyword")
    keyword_answer = top_keyword if top_keyword and top_keyword != "Waiting for performance data" else "No GSC keyword data available yet."
    gbp_answer = (
        f"Use GBP analytics: {summary.get('gbp_impressions_current', 0)} impressions, {summary.get('gbp_clicks_current', 0)} clicks, {summary.get('gbp_engagement_current', 0)} engagement actions."
        if summary.get("gbp_metric_rows_total")
        else "Google Business analytics unavailable for the selected period."
    )
    return [
        {"question": "Best performing synced post", "answer": top_post, "source_type": "Live Data" if summary.get("top_social_post") else "Unavailable Data"},
        {"question": "Lowest performing synced post", "answer": weak_post, "source_type": "Live Data" if summary.get("low_social_post") else "Unavailable Data"},
        {"question": "Website traffic direction", "answer": website_answer, "source_type": "Live Data" if website_current or website_previous else "Unavailable Data"},
        {"question": "Search keyword to prioritize", "answer": keyword_answer, "source_type": "Live Data" if top_keyword and top_keyword != "Waiting for performance data" else "Unavailable Data"},
        {"question": "Google Business action", "answer": gbp_answer, "source_type": "Partial Data" if summary.get("gbp_metric_rows_total") else "Unavailable Data"},
        {"question": "Next content item", "answer": next_content.title if next_content else "No scheduled content item is waiting.", "source_type": "Partial Data" if next_content else "Unavailable Data"},
        {"question": "Landing page gap", "answer": no_landing.keyword if no_landing else "Tracked keyword landing-page coverage is current.", "source_type": "Partial Data" if no_landing else "Live Data"},
        {"question": "Video coverage gap", "answer": no_video.keyword if no_video else "Tracked keywords have video coverage.", "source_type": "Partial Data" if no_video else "Live Data"},
    ]
