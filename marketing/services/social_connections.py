from io import StringIO

from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from marketing.models import OAuthCredential, SocialAccount
from marketing.services.errors import MarketingServiceError
from marketing.services.oauth_connections import (
    GOOGLE_OAUTH_PLATFORMS,
    INSTAGRAM_OAUTH_PLATFORMS,
    META_OAUTH_PLATFORMS,
    get_valid_oauth_access_token,
    oauth_configured,
    oauth_storage_platform,
)
from marketing.utils.activity import log_marketing_sync_failure


SOCIAL_CONNECTION_CONFIG = [
    {
        "key": "facebook",
        "label": "Facebook Pages",
        "api_name": "Meta Graph API",
        "command": "marketing_sync_meta_daily",
        "oauth_supported": True,
        "oauth_label": "Connect Facebook",
        "command_hint": "python manage.py sync_meta_marketing --platform facebook",
        "provider": "meta",
    },
    {
        "key": "instagram",
        "label": "Instagram Business",
        "api_name": "Instagram Graph API",
        "command": "marketing_sync_meta_daily",
        "oauth_supported": True,
        "oauth_label": "Connect Instagram",
        "command_hint": "python manage.py sync_meta_marketing --platform instagram",
        "provider": "instagram",
    },
    {
        "key": "meta_ads",
        "label": "Meta Ads",
        "api_name": "Meta Marketing API",
        "command": "marketing_sync_meta_daily",
        "oauth_supported": True,
        "oauth_label": "Connect Meta Ads",
        "command_hint": "python manage.py marketing_sync_meta_daily",
        "provider": "meta",
    },
    {
        "key": "youtube",
        "label": "YouTube",
        "api_name": "YouTube Data API",
        "command": "marketing_sync_youtube_daily",
        "oauth_supported": True,
        "oauth_label": "Connect YouTube",
        "command_hint": "python manage.py marketing_sync_youtube_daily",
        "provider": "google",
    },
    {
        "key": "ga4",
        "label": "Google Analytics 4",
        "api_name": "Google Analytics Data API",
        "command": "marketing_sync_ga4_daily",
        "oauth_supported": True,
        "oauth_label": "Connect GA4",
        "command_hint": "python manage.py marketing_sync_ga4_daily",
        "provider": "google",
    },
    {
        "key": "gsc",
        "label": "Google Search Console",
        "api_name": "Search Console API",
        "command": "marketing_sync_gsc_daily",
        "oauth_supported": True,
        "oauth_label": "Connect Search Console",
        "command_hint": "python manage.py marketing_sync_gsc_daily",
        "provider": "google",
    },
    {
        "key": "google_business",
        "label": "Google Business Profile",
        "api_name": "Business Profile APIs",
        "command": "marketing_sync_google_business_daily",
        "oauth_supported": True,
        "oauth_label": "Connect Business Profile",
        "command_hint": "python manage.py marketing_sync_google_business_daily",
        "provider": "google",
    },
    {
        "key": "linkedin",
        "label": "LinkedIn Company Pages",
        "api_name": "LinkedIn Marketing API",
        "command": "marketing_sync_linkedin_daily",
        "oauth_supported": True,
        "oauth_label": "Connect LinkedIn",
        "command_hint": "python manage.py sync_linkedin_marketing",
        "provider": "linkedin",
    },
    {
        "key": "tiktok",
        "label": "TikTok Business",
        "api_name": "TikTok Business API",
        "command": "marketing_sync_tiktok_daily",
        "oauth_supported": True,
        "oauth_label": "Connect TikTok",
        "command_hint": "python manage.py sync_tiktok_marketing",
        "provider": "tiktok",
    },
]

SOCIAL_CONNECTION_PLATFORM_KEYS = [item["key"] for item in SOCIAL_CONNECTION_CONFIG]
SOCIAL_CONNECTION_CONFIG_BY_KEY = {item["key"]: item for item in SOCIAL_CONNECTION_CONFIG}
SOCIAL_ACCOUNT_PLATFORM_KEYS = {item[0] for item in SocialAccount.PLATFORM_CHOICES}
SOCIAL_CONNECTION_CREDENTIAL_PLATFORMS = set(SOCIAL_CONNECTION_PLATFORM_KEYS) | {"google", "meta", "facebook", "instagram"}


def social_connection_queryset():
    return OAuthCredential.objects.filter(platform__in=SOCIAL_CONNECTION_CREDENTIAL_PLATFORMS).select_related("platform_account")


def _resolved_last_synced_at(connection: OAuthCredential):
    return connection.last_synced_at or getattr(connection.platform_account, "last_successful_sync", None)


def _resolved_last_sync_status(connection: OAuthCredential) -> str:
    return connection.last_sync_status or getattr(connection.platform_account, "last_sync_status", "")


def _resolved_last_error(connection: OAuthCredential) -> str:
    return connection.last_error or getattr(connection.platform_account, "last_sync_message", "")


def build_connection_cards():
    cards = []
    base_qs = social_connection_queryset().order_by("platform", "-is_active", "-updated_at", "-created_at")
    for config in SOCIAL_CONNECTION_CONFIG:
        storage_platform = oauth_storage_platform(config["key"])
        if config["key"] in GOOGLE_OAUTH_PLATFORMS:
            matches = [item for item in base_qs if item.platform == "google"]
        elif config["key"] in META_OAUTH_PLATFORMS:
            matches = [
                item
                for item in base_qs
                if item.platform in {config["key"], "meta"}
                or (item.platform_account and item.platform_account.platform == config["key"])
            ]
        elif config["key"] in INSTAGRAM_OAUTH_PLATFORMS:
            matches = [
                item
                for item in base_qs
                if item.platform == config["key"]
                or (item.platform_account and item.platform_account.platform == config["key"])
            ]
        else:
            matches = [item for item in base_qs if item.platform == config["key"]]
        primary = matches[0] if matches else None
        cards.append(
            {
                "config": config,
                "connection": primary,
                "storage_platform": storage_platform,
                "connection_count": len(matches),
                "has_access_token": bool(primary and primary.has_access_token),
                "has_refresh_token": bool(primary and primary.has_refresh_token),
                "last_synced_at": _resolved_last_synced_at(primary) if primary else None,
                "last_sync_status": _resolved_last_sync_status(primary) if primary else "",
                "last_error": _resolved_last_error(primary) if primary else "",
                "oauth_configured": oauth_configured(config["key"]),
            }
        )
    return cards


def refresh_connection_snapshot(connection: OAuthCredential, *, save: bool = True):
    account = connection.platform_account
    if not account:
        return connection
    connection.account_name = account.display_name or connection.account_name
    connection.account_id = account.external_account_id or connection.account_id
    connection.is_active = account.is_active
    connection.last_synced_at = account.last_successful_sync or connection.last_synced_at
    connection.last_sync_status = account.last_sync_status or connection.last_sync_status
    connection.last_error = account.last_sync_message or ""
    if save:
        connection.save(
            update_fields=[
                "account_name",
                "account_id",
                "is_active",
                "last_synced_at",
                "last_sync_status",
                "last_error",
                "updated_at",
            ]
        )
    return connection


def update_connection_sync_state(account: SocialAccount, *, status: str, error: str = "", synced_at=None):
    credentials = OAuthCredential.objects.filter(platform_account=account)
    if not credentials.exists():
        credentials = OAuthCredential.objects.filter(
            platform=account.platform,
            account_id=account.external_account_id,
        )

    for connection in credentials:
        connection.account_name = account.display_name or connection.account_name
        connection.account_id = account.external_account_id or connection.account_id
        connection.is_active = account.is_active
        connection.last_sync_status = status
        connection.last_error = error or ""
        if synced_at:
            connection.last_synced_at = synced_at
        connection.save(
            update_fields=[
                "account_name",
                "account_id",
                "is_active",
                "last_synced_at",
                "last_sync_status",
                "last_error",
                "updated_at",
            ]
        )
    if status == "error" and error:
        log_marketing_sync_failure(
            platform=account.platform,
            message=error,
            model_label="marketing.SocialAccount",
            object_id=account.pk,
            meta={"external_account_id": account.external_account_id},
        )


def _upsert_platform_account(connection: OAuthCredential):
    if connection.platform not in SOCIAL_ACCOUNT_PLATFORM_KEYS:
        if connection.platform_account_id:
            connection.platform_account = None
            connection.save(update_fields=["platform_account", "updated_at"])
        return None

    if not connection.account_id:
        connection.platform_account = None
        connection.save(update_fields=["platform_account", "updated_at"])
        return None

    account = SocialAccount.objects.filter(
        platform=connection.platform,
        external_account_id=connection.account_id,
    ).first()
    if not account:
        account = connection.platform_account

    if account:
        account.platform = connection.platform
        account.external_account_id = connection.account_id
        account.display_name = connection.account_name
        account.is_active = connection.is_active
        account.save(update_fields=["platform", "external_account_id", "display_name", "is_active", "updated_at"])
    else:
        account = SocialAccount.objects.create(
            platform=connection.platform,
            external_account_id=connection.account_id,
            display_name=connection.account_name,
            is_active=connection.is_active,
        )

    if connection.platform_account_id != account.id:
        connection.platform_account = account
        connection.save(update_fields=["platform_account", "updated_at"])
    return account


@transaction.atomic
def save_social_connection(*, cleaned_data: dict, existing: OAuthCredential | None = None):
    connection = existing
    if not connection:
        connection = social_connection_queryset().filter(
            platform=cleaned_data["platform"],
            account_id=cleaned_data["account_id"],
        ).first()
    if not connection:
        connection = OAuthCredential(platform=cleaned_data["platform"])

    current_access = connection.get_access_token() if connection.pk else ""
    current_refresh = connection.get_refresh_token() if connection.pk else ""

    access_token = cleaned_data.get("access_token")
    refresh_token = cleaned_data.get("refresh_token")
    resolved_access = current_access if connection.pk and access_token == "" else (access_token or "")
    resolved_refresh = current_refresh if connection.pk and refresh_token == "" else (refresh_token or "")

    connection.platform = cleaned_data["platform"]
    connection.account_name = cleaned_data["account_name"]
    connection.account_id = cleaned_data["account_id"]
    connection.scopes = cleaned_data.get("scopes") or ""
    connection.is_active = bool(cleaned_data.get("is_active"))
    connection.set_tokens(
        access_token=resolved_access,
        refresh_token=resolved_refresh,
        expires_at=cleaned_data.get("token_expires_at"),
    )
    connection.save()

    _upsert_platform_account(connection)
    refresh_connection_snapshot(connection, save=True)
    return connection


def run_social_connection_sync(connection: OAuthCredential):
    config = SOCIAL_CONNECTION_CONFIG_BY_KEY.get(connection.platform)
    if not config and connection.platform == "google":
        config = {
            "command": "marketing_sync_google_daily",
        }
    if not config and connection.platform == "meta":
        config = {
            "command": "marketing_sync_meta_daily",
        }
    if not config:
        raise MarketingServiceError("Unsupported social platform.")
    if not connection.is_active:
        raise MarketingServiceError("Connection is inactive.")
    if connection.platform != "google" and not connection.account_id:
        raise MarketingServiceError("Account ID is required before syncing.")
    get_valid_oauth_access_token(connection)

    kwargs = {}
    if connection.platform in {"facebook", "instagram"}:
        kwargs["account_id"] = connection.account_id
        kwargs["platform"] = connection.platform
    elif connection.platform in SOCIAL_ACCOUNT_PLATFORM_KEYS:
        kwargs["account_id"] = connection.account_id

    buffer = StringIO()
    try:
        call_command(config["command"], stdout=buffer, stderr=buffer, **kwargs)
    except Exception as exc:
        connection.last_sync_status = "error"
        connection.last_error = str(exc)
        connection.save(update_fields=["last_sync_status", "last_error", "updated_at"])
        raise MarketingServiceError(str(exc)) from exc

    connection.refresh_from_db()
    if connection.platform not in SOCIAL_ACCOUNT_PLATFORM_KEYS:
        connection.last_sync_status = "ok"
        connection.last_error = ""
        connection.last_synced_at = timezone.now()
        connection.save(update_fields=["last_sync_status", "last_error", "last_synced_at", "updated_at"])
        return buffer.getvalue().strip()

    refresh_connection_snapshot(connection, save=True)
    if connection.last_sync_status == "error":
        raise MarketingServiceError(connection.last_error or "Sync failed.")
    if not connection.last_sync_status:
        connection.last_sync_status = "ok"
        connection.last_error = ""
        connection.last_synced_at = timezone.now()
        connection.save(update_fields=["last_sync_status", "last_error", "last_synced_at", "updated_at"])
    return buffer.getvalue().strip()


def run_social_platform_sync(platform: str):
    config = SOCIAL_CONNECTION_CONFIG_BY_KEY.get(platform)
    if not config:
        raise MarketingServiceError("Unsupported social platform.")

    storage_platform = oauth_storage_platform(platform)
    credential = OAuthCredential.objects.filter(platform=storage_platform, is_active=True).order_by("-updated_at").first()
    if not credential and platform in {"facebook", "meta_ads"}:
        credential = OAuthCredential.objects.filter(platform="meta", is_active=True).order_by("-updated_at").first()
    if not credential:
        raise MarketingServiceError("Connect this platform before syncing.")
    get_valid_oauth_access_token(credential)

    kwargs = {}
    if platform in {"facebook", "instagram"}:
        kwargs["platform"] = platform
    elif platform == "meta_ads":
        kwargs["platform"] = "meta_ads"

    buffer = StringIO()
    try:
        call_command(config["command"], stdout=buffer, stderr=buffer, **kwargs)
    except Exception as exc:
        credential.last_sync_status = "error"
        credential.last_error = str(exc)
        credential.save(update_fields=["last_sync_status", "last_error", "updated_at"])
        raise MarketingServiceError(str(exc)) from exc

    credential.last_sync_status = "ok"
    credential.last_error = ""
    credential.last_synced_at = timezone.now()
    credential.save(update_fields=["last_sync_status", "last_error", "last_synced_at", "updated_at"])
    return buffer.getvalue().strip()
