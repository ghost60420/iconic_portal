from __future__ import annotations

from django.utils import timezone

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
