from __future__ import annotations

from datetime import timedelta
import base64
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.utils import timezone

from marketing.models import OAuthConnectionRequest, OAuthCredential, SocialAccount
from marketing.services.errors import MarketingServiceError
from marketing.services.google_oauth import (
    build_google_oauth_url,
    fetch_google_userinfo,
    refresh_access_token as refresh_google_access_token,
    save_google_credential,
    sync_google_properties,
)
from marketing.services.oauth_meta import (
    build_meta_oauth_url,
    exchange_code_for_token,
    exchange_long_lived_token,
    fetch_meta_pages,
)


GOOGLE_OAUTH_PLATFORMS = {"google", "ga4", "gsc", "youtube", "google_business"}
META_OAUTH_PLATFORMS = {"meta", "facebook", "instagram", "meta_ads"}
DIRECT_OAUTH_PLATFORMS = {"linkedin", "tiktok"}
OAUTH_START_PLATFORMS = GOOGLE_OAUTH_PLATFORMS | META_OAUTH_PLATFORMS | DIRECT_OAUTH_PLATFORMS


def normalize_oauth_platform(platform: str) -> str:
    normalized = (platform or "").strip().lower()
    if normalized == "facebook_pages":
        return "facebook"
    if normalized == "instagram_business":
        return "instagram"
    if normalized in OAUTH_START_PLATFORMS:
        return normalized
    raise MarketingServiceError("Unsupported OAuth platform.")


def oauth_storage_platform(platform: str) -> str:
    platform = normalize_oauth_platform(platform)
    if platform in GOOGLE_OAUTH_PLATFORMS:
        return "google"
    if platform in META_OAUTH_PLATFORMS:
        return "meta"
    return platform


def oauth_configured(platform: str) -> bool:
    platform = normalize_oauth_platform(platform)
    if platform in GOOGLE_OAUTH_PLATFORMS:
        return bool(
            settings.MARKETING_GOOGLE_CLIENT_ID
            and settings.MARKETING_GOOGLE_CLIENT_SECRET
            and settings.MARKETING_GOOGLE_REDIRECT_URI
        )
    if platform in META_OAUTH_PLATFORMS:
        return bool(
            settings.MARKETING_META_APP_ID
            and settings.MARKETING_META_APP_SECRET
            and settings.MARKETING_META_REDIRECT_URI
        )
    if platform == "linkedin":
        return bool(
            getattr(settings, "MARKETING_LINKEDIN_CLIENT_ID", "")
            and getattr(settings, "MARKETING_LINKEDIN_CLIENT_SECRET", "")
            and getattr(settings, "MARKETING_LINKEDIN_REDIRECT_URI", "")
        )
    if platform == "tiktok":
        return bool(
            getattr(settings, "MARKETING_TIKTOK_CLIENT_KEY", "")
            and getattr(settings, "MARKETING_TIKTOK_CLIENT_SECRET", "")
            and getattr(settings, "MARKETING_TIKTOK_REDIRECT_URI", "")
        )
    return False


def build_oauth_authorization_url(*, platform: str, state: str) -> str:
    platform = normalize_oauth_platform(platform)
    if not oauth_configured(platform):
        raise MarketingServiceError(f"{platform} OAuth is not configured.")

    if platform in GOOGLE_OAUTH_PLATFORMS:
        return build_google_oauth_url(state=state)

    if platform in META_OAUTH_PLATFORMS:
        return build_meta_oauth_url(
            app_id=settings.MARKETING_META_APP_ID,
            redirect_uri=settings.MARKETING_META_REDIRECT_URI,
            state=state,
            scopes=settings.MARKETING_META_SCOPES,
        )

    if platform == "linkedin":
        params = {
            "response_type": "code",
            "client_id": settings.MARKETING_LINKEDIN_CLIENT_ID,
            "redirect_uri": settings.MARKETING_LINKEDIN_REDIRECT_URI,
            "state": state,
            "scope": " ".join(settings.MARKETING_LINKEDIN_SCOPES),
        }
        return f"{settings.MARKETING_LINKEDIN_AUTHORIZE_URL}?{urlencode(params)}"

    if platform == "tiktok":
        params = {
            "response_type": "code",
            "client_key": settings.MARKETING_TIKTOK_CLIENT_KEY,
            "redirect_uri": settings.MARKETING_TIKTOK_REDIRECT_URI,
            "state": state,
            "scope": ",".join(settings.MARKETING_TIKTOK_SCOPES),
        }
        return f"{settings.MARKETING_TIKTOK_AUTHORIZE_URL}?{urlencode(params)}"

    raise MarketingServiceError("Unsupported OAuth platform.")


def _post_form(url: str, payload: dict, *, headers: dict | None = None) -> dict:
    request = Request(
        url,
        data=urlencode(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8") or "{}"
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"OAuth token error {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise MarketingServiceError(f"OAuth token request failed: {exc.reason}") from exc
    return json.loads(raw)


def _request_json(url: str, *, access_token: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8") or "{}"
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"OAuth profile error {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise MarketingServiceError(f"OAuth profile request failed: {exc.reason}") from exc
    return json.loads(raw)


def _token_expiry(payload: dict) -> object | None:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    try:
        return timezone.now() + timedelta(seconds=max(int(expires_in) - 60, 60))
    except (TypeError, ValueError):
        return None


def _decode_jwt_payload(token: str) -> dict:
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}


def _save_direct_credential(*, platform: str, token_payload: dict) -> OAuthCredential:
    access_token = token_payload.get("access_token") or ""
    refresh_token = token_payload.get("refresh_token") or ""
    if not access_token:
        raise MarketingServiceError(f"{platform} did not return an access token.")

    profile = {}
    if platform == "linkedin":
        try:
            profile = _request_json(settings.MARKETING_LINKEDIN_USERINFO_URL, access_token=access_token)
        except MarketingServiceError:
            profile = _decode_jwt_payload(token_payload.get("id_token") or "")

    account_id = (
        profile.get("sub")
        or profile.get("id")
        or token_payload.get("open_id")
        or token_payload.get("union_id")
        or token_payload.get("account_id")
        or platform
    )
    account_name = (
        profile.get("email")
        or profile.get("name")
        or profile.get("localizedFirstName")
        or token_payload.get("account_name")
        or platform.title()
    )
    credential = (
        OAuthCredential.objects.filter(platform=platform, account_id=account_id).first()
        or OAuthCredential.objects.filter(platform=platform, account_name=account_name).first()
        or OAuthCredential(platform=platform)
    )
    existing_refresh = credential.get_refresh_token() if credential.pk else ""
    credential.account_id = account_id
    credential.account_name = account_name
    credential.scopes = token_payload.get("scope") or " ".join(_direct_scopes(platform))
    credential.is_active = True
    credential.last_sync_status = "connected"
    credential.last_error = ""
    credential.set_tokens(
        access_token=access_token,
        refresh_token=refresh_token or existing_refresh,
        expires_at=_token_expiry(token_payload),
    )
    credential.save()
    return credential


def _direct_scopes(platform: str) -> list[str]:
    if platform == "linkedin":
        return list(settings.MARKETING_LINKEDIN_SCOPES)
    if platform == "tiktok":
        return list(settings.MARKETING_TIKTOK_SCOPES)
    return []


def exchange_direct_oauth_code(*, platform: str, code: str) -> OAuthCredential:
    platform = normalize_oauth_platform(platform)
    if platform == "linkedin":
        token_payload = _post_form(
            settings.MARKETING_LINKEDIN_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.MARKETING_LINKEDIN_REDIRECT_URI,
                "client_id": settings.MARKETING_LINKEDIN_CLIENT_ID,
                "client_secret": settings.MARKETING_LINKEDIN_CLIENT_SECRET,
            },
        )
        return _save_direct_credential(platform=platform, token_payload=token_payload)

    if platform == "tiktok":
        token_payload = _post_form(
            settings.MARKETING_TIKTOK_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.MARKETING_TIKTOK_REDIRECT_URI,
                "client_key": settings.MARKETING_TIKTOK_CLIENT_KEY,
                "client_secret": settings.MARKETING_TIKTOK_CLIENT_SECRET,
            },
        )
        return _save_direct_credential(platform=platform, token_payload=token_payload)

    raise MarketingServiceError("Unsupported direct OAuth platform.")


def complete_meta_oauth_request(conn: OAuthConnectionRequest) -> dict:
    if conn.platform not in META_OAUTH_PLATFORMS:
        raise MarketingServiceError("OAuth request is not a Meta request.")
    if not conn.code:
        raise MarketingServiceError("Missing Meta authorization code.")

    token_payload = exchange_code_for_token(
        app_id=settings.MARKETING_META_APP_ID,
        app_secret=settings.MARKETING_META_APP_SECRET,
        redirect_uri=settings.MARKETING_META_REDIRECT_URI,
        code=conn.code,
    )
    short_token = token_payload.get("access_token")
    if not short_token:
        raise MarketingServiceError("Meta token exchange failed.")

    long_payload = exchange_long_lived_token(
        app_id=settings.MARKETING_META_APP_ID,
        app_secret=settings.MARKETING_META_APP_SECRET,
        access_token=short_token,
    )
    access_token = long_payload.get("access_token") or short_token
    expires_at = _token_expiry(long_payload) or _token_expiry(token_payload)

    platform_cred, _ = OAuthCredential.objects.get_or_create(platform="meta", platform_account=None)
    platform_cred.set_tokens(access_token=access_token, refresh_token="", expires_at=expires_at)
    platform_cred.account_name = "Meta Platform Token"
    platform_cred.account_id = "meta"
    platform_cred.is_active = True
    platform_cred.scopes = ",".join(settings.MARKETING_META_SCOPES)
    platform_cred.last_sync_status = "connected"
    platform_cred.last_error = ""
    platform_cred.save()

    pages = fetch_meta_pages(access_token=access_token)
    facebook_count = 0
    instagram_count = 0
    for page in pages:
        page_id = page.get("id")
        if not page_id:
            continue
        name = page.get("name") or "Facebook Page"
        timezone_name = page.get("timezone") or ""
        fb_account, _ = SocialAccount.objects.update_or_create(
            platform="facebook",
            external_account_id=page_id,
            defaults={"display_name": name, "timezone": timezone_name, "is_active": True},
        )
        fb_cred, _ = OAuthCredential.objects.get_or_create(platform="facebook", platform_account=fb_account)
        fb_cred.set_tokens(access_token=access_token, refresh_token="", expires_at=expires_at)
        fb_cred.account_name = name
        fb_cred.account_id = page_id
        fb_cred.is_active = True
        fb_cred.scopes = ",".join(settings.MARKETING_META_SCOPES)
        fb_cred.last_sync_status = "connected"
        fb_cred.last_error = ""
        fb_cred.save()
        facebook_count += 1

        ig_info = page.get("instagram_business_account") or {}
        ig_id = ig_info.get("id")
        if ig_id:
            ig_account, _ = SocialAccount.objects.update_or_create(
                platform="instagram",
                external_account_id=ig_id,
                defaults={"display_name": f"{name} (Instagram)", "timezone": timezone_name, "is_active": True},
            )
            ig_cred, _ = OAuthCredential.objects.get_or_create(platform="instagram", platform_account=ig_account)
            ig_cred.set_tokens(access_token=access_token, refresh_token="", expires_at=expires_at)
            ig_cred.account_name = f"{name} (Instagram)"
            ig_cred.account_id = ig_id
            ig_cred.is_active = True
            ig_cred.scopes = ",".join(settings.MARKETING_META_SCOPES)
            ig_cred.last_sync_status = "connected"
            ig_cred.last_error = ""
            ig_cred.save()
            instagram_count += 1

    conn.status = "completed"
    conn.error_message = ""
    conn.save(update_fields=["status", "error_message", "updated_at"])
    return {"facebook_count": facebook_count, "instagram_count": instagram_count}


def complete_google_oauth(*, conn: OAuthConnectionRequest, token_payload: dict) -> dict:
    userinfo = fetch_google_userinfo(token_payload.get("access_token", ""))
    credential = save_google_credential(token_payload=token_payload, userinfo=userinfo)
    discovery = sync_google_properties(credential=credential)
    conn.status = "completed"
    conn.error_message = ""
    conn.save(update_fields=["status", "error_message", "updated_at"])
    return {"credential": credential, **discovery}


def get_valid_oauth_access_token(credential: OAuthCredential) -> str:
    if not credential:
        raise MarketingServiceError("OAuth credential is missing.")
    if credential.platform == "google":
        token = credential.get_access_token()
        return refresh_google_access_token(credential) if _needs_refresh(credential) or not token else token
    if credential.platform in DIRECT_OAUTH_PLATFORMS:
        token = credential.get_access_token()
        return _refresh_direct_access_token(credential) if _needs_refresh(credential) or not token else token
    token = credential.get_access_token()
    if token:
        return token
    raise MarketingServiceError("Access token is missing. Reconnect this platform.")


def _needs_refresh(credential: OAuthCredential) -> bool:
    return bool(credential.expires_at and credential.expires_at <= timezone.now() + timedelta(minutes=2))


def _refresh_direct_access_token(credential: OAuthCredential) -> str:
    refresh_token = credential.get_refresh_token()
    if not refresh_token:
        raise MarketingServiceError(f"{credential.get_platform_display()} refresh token is missing. Reconnect.")
    platform = credential.platform
    if platform == "linkedin":
        payload = _post_form(
            settings.MARKETING_LINKEDIN_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.MARKETING_LINKEDIN_CLIENT_ID,
                "client_secret": settings.MARKETING_LINKEDIN_CLIENT_SECRET,
            },
        )
    elif platform == "tiktok":
        payload = _post_form(
            settings.MARKETING_TIKTOK_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_key": settings.MARKETING_TIKTOK_CLIENT_KEY,
                "client_secret": settings.MARKETING_TIKTOK_CLIENT_SECRET,
            },
        )
    else:
        raise MarketingServiceError("Unsupported token refresh platform.")
    access_token = payload.get("access_token") or ""
    if not access_token:
        raise MarketingServiceError("OAuth refresh did not return an access token.")
    credential.set_tokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token") or refresh_token,
        expires_at=_token_expiry(payload),
    )
    credential.save(update_fields=["encrypted_access_token", "encrypted_refresh_token", "expires_at", "updated_at"])
    return access_token


def token_for_social_account(account: SocialAccount) -> str:
    credential = OAuthCredential.objects.filter(platform_account=account, is_active=True).first()
    if credential:
        return get_valid_oauth_access_token(credential)
    credential = OAuthCredential.objects.filter(platform=account.platform, is_active=True).order_by("-updated_at").first()
    if credential:
        return get_valid_oauth_access_token(credential)
    if account.platform in {"facebook", "instagram", "meta_ads", "meta_business"}:
        credential = OAuthCredential.objects.filter(platform="meta", is_active=True).order_by("-updated_at").first()
        if credential:
            return get_valid_oauth_access_token(credential)
    if account.platform in {"youtube", "google_business"}:
        credential = OAuthCredential.objects.filter(platform="google", is_active=True).order_by("-updated_at").first()
        if credential:
            return get_valid_oauth_access_token(credential)
    raise MarketingServiceError(f"No active OAuth token found for {account.get_platform_display()}.")
