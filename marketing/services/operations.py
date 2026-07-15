from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db.models import Max
from django.utils import timezone

from crm.models import SystemActivityLog
from marketing.models import (
    AccountMetricDaily,
    AdAccount,
    AdCampaign,
    AdMetricDaily,
    OAuthCredential,
    SeoPageDaily,
    SeoProperty,
    SeoQueryDaily,
    SocialAccount,
    SocialAudienceDaily,
    SocialContent,
    SocialMetricDaily,
    WebsitePageDaily,
    WebsiteTrafficDaily,
)
from marketing.services.errors import MarketingServiceError
from marketing.services.intelligence import linkedin_connection_status
from marketing.services.oauth_connections import oauth_configured
from marketing.utils.activity import log_marketing_activity


OPERATIONS_SYNC_COMMANDS = {
    "facebook": {"command": "marketing_sync_meta_daily", "kwargs": {"platform": "facebook"}},
    "instagram": {"command": "marketing_sync_meta_daily", "kwargs": {"platform": "instagram"}},
    "ga4": {"command": "marketing_sync_ga4_daily", "kwargs": {}},
    "gsc": {"command": "marketing_sync_gsc_daily", "kwargs": {}},
    "google_business": {"command": "marketing_sync_google_business_daily", "kwargs": {}},
    "youtube": {"command": "marketing_sync_youtube_daily", "kwargs": {}},
}

OPERATIONS_SCHEDULE = {
    "ga4": "09:10",
    "gsc": "09:25",
    "youtube": "09:40",
    "facebook": "09:55",
    "instagram": "09:55",
    "meta_ads": "09:55",
    "google_business": "10:10",
}

OPERATIONS_PLATFORMS = [
    {"key": "facebook", "label": "Facebook Pages", "manual_sync": True},
    {"key": "instagram", "label": "Instagram Business", "manual_sync": True},
    {"key": "ga4", "label": "Google Analytics 4", "manual_sync": True},
    {"key": "gsc", "label": "Google Search Console", "manual_sync": True},
    {"key": "google_business", "label": "Google Business Profile", "manual_sync": True},
    {"key": "youtube", "label": "YouTube", "manual_sync": True},
    {"key": "meta_ads", "label": "Meta Ads", "manual_sync": False},
    {"key": "linkedin", "label": "LinkedIn", "manual_sync": False},
    {"key": "tiktok", "label": "TikTok", "manual_sync": False},
]


def _credential_for(platform: str):
    if platform in {"ga4", "gsc", "google_business", "youtube"}:
        return OAuthCredential.objects.filter(platform="google", is_active=True).order_by("-updated_at").first()
    if platform in {"facebook", "meta_ads"}:
        return OAuthCredential.objects.filter(platform="meta", is_active=True).order_by("-updated_at").first()
    if platform == "instagram":
        return OAuthCredential.objects.filter(platform="instagram", is_active=True).order_by("-updated_at").first()
    return OAuthCredential.objects.filter(platform=platform, is_active=True).order_by("-updated_at").first()


def _social_last_success(platform: str):
    return SocialAccount.objects.filter(platform=platform, is_active=True).aggregate(
        latest=Max("last_successful_sync")
    )["latest"]


def _latest_of(*values):
    present = [value for value in values if value]
    return max(present) if present else None


def _next_run_at(time_text: str):
    if not time_text:
        return None
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    now = timezone.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _seo_property_last(*, ga4: bool = False, gsc: bool = False):
    qs = SeoProperty.objects.filter(is_active=True)
    if ga4:
        qs = qs.exclude(ga4_property_id="")
    if gsc:
        qs = qs.exclude(gsc_site_url="")
    return qs.aggregate(latest=Max("last_sync_at"))["latest"]


def _platform_counts(platform: str) -> list[dict]:
    if platform == "ga4":
        return [
            {"label": "Traffic rows", "value": WebsiteTrafficDaily.objects.count()},
            {"label": "Page rows", "value": WebsitePageDaily.objects.count()},
        ]
    if platform == "gsc":
        return [
            {"label": "Query rows", "value": SeoQueryDaily.objects.count()},
            {"label": "Page rows", "value": SeoPageDaily.objects.count()},
        ]
    if platform == "meta_ads":
        return [
            {"label": "Ad accounts", "value": AdAccount.objects.count()},
            {"label": "Campaigns", "value": AdCampaign.objects.count()},
            {"label": "Metric rows", "value": AdMetricDaily.objects.count()},
        ]
    social_platform = platform
    return [
        {"label": "Accounts", "value": SocialAccount.objects.filter(platform=social_platform).count()},
        {"label": "Account metric rows", "value": AccountMetricDaily.objects.filter(account__platform=social_platform).count()},
        {"label": "Content rows", "value": SocialContent.objects.filter(platform=social_platform).count()},
        {"label": "Content metric rows", "value": SocialMetricDaily.objects.filter(content__platform=social_platform).count()},
        {"label": "Audience rows", "value": SocialAudienceDaily.objects.filter(account__platform=social_platform).count()},
    ]


def _platform_latest_data(platform: str):
    if platform == "ga4":
        return _latest_of(
            WebsiteTrafficDaily.objects.aggregate(latest=Max("date"))["latest"],
            WebsitePageDaily.objects.aggregate(latest=Max("date"))["latest"],
        )
    if platform == "gsc":
        return _latest_of(
            SeoQueryDaily.objects.aggregate(latest=Max("date"))["latest"],
            SeoPageDaily.objects.aggregate(latest=Max("date"))["latest"],
        )
    if platform == "meta_ads":
        return AdMetricDaily.objects.aggregate(latest=Max("date"))["latest"]
    return _latest_of(
        AccountMetricDaily.objects.filter(account__platform=platform).aggregate(latest=Max("date"))["latest"],
        SocialMetricDaily.objects.filter(content__platform=platform).aggregate(latest=Max("date"))["latest"],
        SocialAudienceDaily.objects.filter(account__platform=platform).aggregate(latest=Max("date"))["latest"],
    )


def _platform_last_sync(platform: str):
    credential = _credential_for(platform)
    if platform == "ga4":
        return _latest_of(_seo_property_last(ga4=True), getattr(credential, "last_synced_at", None))
    if platform == "gsc":
        return _latest_of(_seo_property_last(gsc=True), getattr(credential, "last_synced_at", None))
    if platform == "meta_ads":
        return _latest_of(_social_last_success("meta_business"), getattr(credential, "last_synced_at", None))
    if platform in {"facebook", "instagram", "youtube", "google_business"}:
        return _latest_of(_social_last_success(platform), getattr(credential, "last_synced_at", None))
    return getattr(credential, "last_synced_at", None)


def _base_status(platform: str, credential) -> tuple[str, str, str]:
    if platform == "linkedin":
        readiness = linkedin_connection_status(credential)
        if readiness.get("approval_required"):
            return "Waiting Approval", "warn", readiness["message"]
    if not oauth_configured(platform):
        return "Not Configured", "neutral", "OAuth settings are not configured."
    if not credential or not credential.is_active or not credential.has_access_token:
        return "Not Configured", "neutral", "No active credential is saved."
    error = getattr(credential, "last_error", "") or ""
    if "SERVICE_DISABLED" in error or "PERMISSION_DENIED" in error:
        return "API Blocked", "bad", error
    if error or getattr(credential, "last_sync_status", "") == "error":
        return "Partially Connected", "warn", error or "The last sync reported an error."
    return "Connected", "good", "OAuth credential is active."


def _google_business_feature_statuses() -> list[dict]:
    analytics_rows = AccountMetricDaily.objects.filter(account__platform="google_business").count()
    content_rows = SocialContent.objects.filter(platform="google_business").count()
    credential = _credential_for("google_business")
    error_text = ""
    account_error = (
        SocialAccount.objects.filter(platform="google_business", is_active=True)
        .exclude(last_sync_message="")
        .values_list("last_sync_message", flat=True)
        .first()
    )
    if account_error:
        error_text = account_error
    elif credential:
        error_text = credential.last_error or ""
    blocked = "SERVICE_DISABLED" in error_text or "Google My Business API" in error_text
    return [
        {"label": "Profile Connected", "status": "Connected", "tone": "good"},
        {"label": "Analytics Working", "status": "Working" if analytics_rows else "Waiting for sync", "tone": "good" if analytics_rows else "warn"},
        {"label": "Reviews", "status": "Unavailable" if blocked or not content_rows else "Imported", "tone": "warn" if blocked or not content_rows else "good"},
        {"label": "Posts", "status": "Unavailable" if blocked or not content_rows else "Imported", "tone": "warn" if blocked or not content_rows else "good"},
    ]


def _row_warning(platform: str) -> str:
    if platform == "meta_ads" and AdCampaign.objects.filter(status__iexact="ACTIVE").exists() and not AdMetricDaily.objects.exists():
        return "Connected. No Recent Ad Activity returned by Meta Graph API."
    if platform in {"linkedin", "tiktok"}:
        return ""
    if not _platform_latest_data(platform):
        return "No data available. Connect the platform or run a sync before relying on this card."
    return ""


def build_marketing_operations_context(*, include_logs: bool = False) -> dict:
    rows = []
    for config in OPERATIONS_PLATFORMS:
        platform = config["key"]
        credential = _credential_for(platform)
        status_label, tone, health = _base_status(platform, credential)
        feature_statuses = []
        if platform == "google_business":
            feature_statuses = _google_business_feature_statuses()
            if any(item["status"] == "Unavailable" for item in feature_statuses):
                status_label = "Partially Connected"
                tone = "warn"
                health = "Profile and analytics are connected; reviews/posts are blocked by the Google My Business API."
        if platform == "meta_ads" and AdCampaign.objects.exists() and not AdMetricDaily.objects.exists():
            status_label = "Connected"
            tone = "good"
            health = "No Recent Ad Activity returned by Meta Graph API."

        schedule_text = OPERATIONS_SCHEDULE.get(platform, "")
        rows.append(
            {
                "key": platform,
                "label": config["label"],
                "status_label": status_label,
                "tone": tone,
                "api_health": health,
                "last_sync": _platform_last_sync(platform),
                "last_successful_sync": _platform_last_sync(platform) if status_label in {"Connected", "Partially Connected"} else None,
                "latest_data_date": _platform_latest_data(platform),
                "row_counts": _platform_counts(platform),
                "feature_statuses": feature_statuses,
                "next_run": _next_run_at(schedule_text),
                "schedule_text": f"{schedule_text} UTC" if schedule_text else "Not scheduled",
                "manual_sync": config["manual_sync"],
                "warning": _row_warning(platform),
            }
        )
    logs = []
    if include_logs:
        logs = list(SystemActivityLog.objects.filter(area="marketing").order_by("-created_at")[:60])
    return {"platform_rows": rows, "marketing_logs": logs}


def run_marketing_operations_sync(*, platform: str, user=None) -> str:
    config = OPERATIONS_SYNC_COMMANDS.get(platform)
    if not config:
        raise MarketingServiceError("Manual sync is not enabled for this platform.")

    buffer = StringIO()
    try:
        call_command(config["command"], stdout=buffer, stderr=buffer, **config["kwargs"])
    except Exception as exc:
        log_marketing_activity(
            user=user,
            action="marketing_manual_sync",
            level="error",
            message=f"{platform} manual sync failed: {exc}",
            meta={"platform": platform},
        )
        raise MarketingServiceError(str(exc)) from exc

    output = buffer.getvalue().strip()
    log_marketing_activity(
        user=user,
        action="marketing_manual_sync",
        level="info",
        message=f"{platform} manual sync completed.",
        meta={"platform": platform, "output": output[-1200:]},
    )
    return output
