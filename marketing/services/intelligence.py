from __future__ import annotations

import re
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from marketing.models import MarketingContentIdea, MarketingKeywordPlan, MarketingVideoIdea, SeoQueryDaily

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
        {
            "key": "google_trends",
            "label": "Google Trends",
            "status_label": "Manual framework",
            "tone": "neutral",
            "message": "Manual trend and keyword research is available; no external API is called.",
        },
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
          (SELECT query FROM {seo_table} GROUP BY query ORDER BY SUM(clicks) DESC, SUM(impressions) DESC LIMIT 1)
    """
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    params = [
        month_start, month_end, month_start, month_end,
        month_start.date(), month_end.date(), month_start.date(), month_end.date(),
        today, week_end, today, week_end, today, today,
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

    seo = min(100, snapshot["seo_keywords"] * 4 + ratio(snapshot["keywords_with_landing"], snapshot["seo_keywords"]) // 2)
    content = ratio(snapshot["published_blogs"] + snapshot["published_videos"], snapshot["content_total"] + snapshot["video_total"])
    active_platforms = sum(
        bool(snapshot[key])
        for key in ("google_business_posts", "linkedin_posts", "instagram_posts", "tiktok_ideas", "email_campaigns")
    )
    social = min(100, active_platforms * 20)
    consistency = ratio(snapshot["completed_this_month"], max(snapshot["planned_this_month"], 1))
    website = ratio(snapshot["keywords_with_landing"], snapshot["seo_keywords"])
    google_business = min(100, snapshot["google_business_posts"] * 15 + snapshot["published_google_business_posts"] * 20)
    values = [
        ("SEO Score", seo, "Planning based: SEO keyword rows and landing-page coverage."),
        ("Content Score", content, "Planning based: published blog and video plan rows."),
        ("Social Score", social, "Planning based: internal platform content plan coverage."),
        ("Consistency Score", consistency, "Planning based: completed vs planned content this month."),
        ("Website Score", website, "Planning based: keyword landing-page coverage, not GA4 traffic."),
        ("Google Business Score", google_business, "Planning based: internal Google Business post plans, not API posts."),
    ]
    overall = round(sum(score for _label, score, _basis in values) / len(values))
    values.append(("Overall Marketing Health", overall, "Mixed planning score. Live synced performance remains on the dashboard."))
    return [
        {
            "label": label,
            "score": score,
            "basis": basis,
            "source_type": "Internal planning recommendation",
            "tone": "green" if score >= 70 else "yellow" if score >= 40 else "red",
        }
        for label, score, basis in values
    ]


def build_assistant_answers(*, keywords, content_ideas, video_ideas) -> list[dict]:
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
    return [
        {"question": "What should we post today?", "answer": next_content.title if next_content else "Create the first approved content brief."},
        {"question": "What keyword should we target?", "answer": target.keyword if target else "Add and prioritize an SEO keyword."},
        {"question": "What blog should we write?", "answer": no_article.suggested_article or f"A practical guide to {no_article.keyword}" if no_article else "All tracked keywords have a blog plan."},
        {"question": "What video should we film?", "answer": no_video.suggested_video or f"What buyers should know about {no_video.keyword}" if no_video else "All tracked keywords have a video plan."},
        {"question": "Which landing page needs SEO?", "answer": no_landing.keyword if no_landing else "Every tracked keyword has a landing-page suggestion."},
        {"question": "Which product has no content?", "answer": target.get_product_category_display() if target and not next_content else "Review category coverage in the content calendar."},
        {"question": "Which keyword has no article?", "answer": no_article.keyword if no_article else "None."},
        {"question": "Which keyword has no video?", "answer": no_video.keyword if no_video else "None."},
    ]
