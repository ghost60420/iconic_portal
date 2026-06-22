from datetime import timedelta
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.utils import timezone

from marketing.models import OAuthCredential, SeoProperty, SocialAccount
from marketing.services.errors import MarketingServiceError
from marketing.utils.activity import log_marketing_sync_failure


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GA4_ACCOUNT_SUMMARIES_URL = "https://analyticsadmin.googleapis.com/v1alpha/accountSummaries"
GSC_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
GBP_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
GBP_BUSINESS_INFORMATION_URL = "https://mybusinessbusinessinformation.googleapis.com/v1"


def google_oauth_configured() -> bool:
    return bool(
        settings.MARKETING_GOOGLE_CLIENT_ID
        and settings.MARKETING_GOOGLE_CLIENT_SECRET
        and settings.MARKETING_GOOGLE_REDIRECT_URI
    )


def build_google_oauth_url(*, state: str) -> str:
    if not google_oauth_configured():
        raise MarketingServiceError("Google OAuth is not configured.")
    params = {
        "client_id": settings.MARKETING_GOOGLE_CLIENT_ID,
        "redirect_uri": settings.MARKETING_GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(settings.MARKETING_GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None, access_token: str = "") -> dict:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        message = f"Google API error {exc.code}: {detail[:500]}"
        log_marketing_sync_failure(platform="google", message=message, meta={"url": url.split("?")[0], "status_code": exc.code})
        raise MarketingServiceError(message) from exc
    except URLError as exc:
        message = f"Google API request failed: {exc.reason}"
        log_marketing_sync_failure(platform="google", message=message, meta={"url": url.split("?")[0]})
        raise MarketingServiceError(message) from exc

    if not raw:
        return {}
    return json.loads(raw)


def google_api_request_json(url: str, *, method: str = "GET", payload: dict | None = None, access_token: str = "") -> dict:
    return _request_json(url, method=method, payload=payload, access_token=access_token)


def _post_form(url: str, payload: dict) -> dict:
    body = urlencode(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        message = f"Google OAuth error {exc.code}: {detail[:500]}"
        log_marketing_sync_failure(platform="google", message=message, meta={"url": url, "status_code": exc.code})
        raise MarketingServiceError(message) from exc
    except URLError as exc:
        message = f"Google OAuth request failed: {exc.reason}"
        log_marketing_sync_failure(platform="google", message=message, meta={"url": url})
        raise MarketingServiceError(message) from exc


def exchange_code_for_tokens(code: str) -> dict:
    if not code:
        raise MarketingServiceError("Missing Google authorization code.")
    return _post_form(
        GOOGLE_TOKEN_URL,
        {
            "code": code,
            "client_id": settings.MARKETING_GOOGLE_CLIENT_ID,
            "client_secret": settings.MARKETING_GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.MARKETING_GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )


def refresh_access_token(credential: OAuthCredential) -> str:
    refresh_token = credential.get_refresh_token()
    if not refresh_token:
        raise MarketingServiceError("Google refresh token is missing. Reconnect Google.")
    token_payload = _post_form(
        GOOGLE_TOKEN_URL,
        {
            "client_id": settings.MARKETING_GOOGLE_CLIENT_ID,
            "client_secret": settings.MARKETING_GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    access_token = token_payload.get("access_token") or ""
    if not access_token:
        raise MarketingServiceError("Google did not return an access token.")
    expires_in = int(token_payload.get("expires_in") or 3600)
    credential.set_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=timezone.now() + timedelta(seconds=max(expires_in - 60, 60)),
    )
    credential.save(update_fields=["encrypted_access_token", "encrypted_refresh_token", "expires_at", "updated_at"])
    return access_token


def get_google_credential(*, fallback_platform: str = "") -> OAuthCredential | None:
    credential = OAuthCredential.objects.filter(platform="google", is_active=True).order_by("-updated_at").first()
    if credential:
        return credential
    if fallback_platform:
        return OAuthCredential.objects.filter(platform=fallback_platform, is_active=True).order_by("-updated_at").first()
    return None


def get_valid_access_token(credential: OAuthCredential) -> str:
    if not credential:
        raise MarketingServiceError("Google credentials are not connected.")
    if credential.expires_at and credential.expires_at <= timezone.now() + timedelta(minutes=2):
        return refresh_access_token(credential)
    token = credential.get_access_token()
    if token:
        return token
    return refresh_access_token(credential)


def fetch_google_userinfo(access_token: str) -> dict:
    return _request_json(GOOGLE_USERINFO_URL, access_token=access_token)


def save_google_credential(*, token_payload: dict, userinfo: dict) -> OAuthCredential:
    email = userinfo.get("email") or ""
    account_id = userinfo.get("sub") or email or "google"
    credential = (
        OAuthCredential.objects.filter(platform="google", account_id=account_id).first()
        or OAuthCredential.objects.filter(platform="google", account_name=email).first()
        or OAuthCredential(platform="google")
    )
    existing_refresh = credential.get_refresh_token() if credential.pk else ""
    refresh_token = token_payload.get("refresh_token") or existing_refresh
    access_token = token_payload.get("access_token") or ""
    if not access_token:
        raise MarketingServiceError("Google did not return an access token.")
    expires_in = int(token_payload.get("expires_in") or 3600)
    credential.account_name = email or userinfo.get("name") or "Google Account"
    credential.account_id = account_id
    credential.scopes = token_payload.get("scope") or " ".join(settings.MARKETING_GOOGLE_SCOPES)
    credential.is_active = True
    credential.last_error = ""
    credential.last_sync_status = "connected"
    credential.set_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=timezone.now() + timedelta(seconds=max(expires_in - 60, 60)),
    )
    credential.save()
    return credential


def list_ga4_properties(access_token: str) -> list[dict]:
    properties = []
    page_token = ""
    while True:
        url = GA4_ACCOUNT_SUMMARIES_URL
        if page_token:
            url = f"{url}?{urlencode({'pageToken': page_token})}"
        payload = _request_json(url, access_token=access_token)
        for account in payload.get("accountSummaries", []):
            account_name = account.get("displayName") or account.get("account") or ""
            for prop in account.get("propertySummaries", []):
                raw_property = prop.get("property") or ""
                property_id = raw_property.replace("properties/", "")
                if property_id:
                    properties.append(
                        {
                            "property_id": property_id,
                            "display_name": prop.get("displayName") or property_id,
                            "account_name": account_name,
                            "property_resource": raw_property,
                        }
                    )
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    return properties


def list_gsc_sites(access_token: str) -> list[dict]:
    payload = _request_json(GSC_SITES_URL, access_token=access_token)
    sites = []
    for site in payload.get("siteEntry", []):
        site_url = site.get("siteUrl") or ""
        if site_url:
            sites.append(
                {
                    "site_url": site_url,
                    "permission_level": site.get("permissionLevel") or "",
                }
            )
    return sites


def list_youtube_channels(access_token: str) -> list[dict]:
    params = {
        "part": "snippet,statistics",
        "mine": "true",
        "maxResults": "50",
    }
    payload = _request_json(f"{YOUTUBE_CHANNELS_URL}?{urlencode(params)}", access_token=access_token)
    channels = []
    for item in payload.get("items", []):
        channel_id = item.get("id") or ""
        snippet = item.get("snippet") or {}
        if channel_id:
            channels.append(
                {
                    "channel_id": channel_id,
                    "title": snippet.get("title") or channel_id,
                }
            )
    return channels


def list_google_business_locations(access_token: str) -> list[dict]:
    locations = []
    account_payload = _request_json(GBP_ACCOUNTS_URL, access_token=access_token)
    for account in account_payload.get("accounts", []):
        account_name = account.get("name") or ""
        if not account_name:
            continue
        params = {
            "readMask": "name,title,metadata",
        }
        url = f"{GBP_BUSINESS_INFORMATION_URL}/{account_name}/locations?{urlencode(params)}"
        location_payload = _request_json(url, access_token=access_token)
        for location in location_payload.get("locations", []):
            location_name = location.get("name") or ""
            if location_name:
                locations.append(
                    {
                        "location_name": location_name,
                        "title": location.get("title") or location_name,
                        "account_name": account.get("accountName") or account_name,
                    }
                )
    return locations


def _safe_google_discovery(label: str, discovery_func, access_token: str) -> tuple[list[dict], str]:
    try:
        return discovery_func(access_token), ""
    except MarketingServiceError as exc:
        return [], f"{label}: {exc}"


def sync_google_properties(*, credential: OAuthCredential) -> dict:
    access_token = get_valid_access_token(credential)
    discovery_errors = []
    ga4_properties, error = _safe_google_discovery("GA4 discovery failed", list_ga4_properties, access_token)
    if error:
        discovery_errors.append(error)
    gsc_sites, error = _safe_google_discovery("Search Console discovery failed", list_gsc_sites, access_token)
    if error:
        discovery_errors.append(error)
    youtube_channels, error = _safe_google_discovery("YouTube discovery failed", list_youtube_channels, access_token)
    if error:
        discovery_errors.append(error)
    google_business_locations, error = _safe_google_discovery(
        "Google Business Profile discovery failed",
        list_google_business_locations,
        access_token,
    )
    if error:
        discovery_errors.append(error)

    updated_properties = []

    for item in ga4_properties:
        prop, _ = SeoProperty.objects.update_or_create(
            ga4_property_id=item["property_id"],
            defaults={
                "name": item["display_name"],
                "is_active": True,
            },
        )
        updated_properties.append(prop)

    for item in gsc_sites:
        prop = SeoProperty.objects.filter(gsc_site_url=item["site_url"]).first()
        if not prop and len(ga4_properties) == 1:
            prop = SeoProperty.objects.filter(ga4_property_id=ga4_properties[0]["property_id"]).first()
        if prop:
            prop.gsc_site_url = item["site_url"]
            if not prop.name:
                prop.name = item["site_url"]
            prop.is_active = True
            prop.save(update_fields=["gsc_site_url", "name", "is_active", "updated_at"])
        else:
            prop = SeoProperty.objects.create(
                name=item["site_url"],
                gsc_site_url=item["site_url"],
                is_active=True,
            )
        updated_properties.append(prop)

    youtube_accounts = []
    for item in youtube_channels:
        account, _ = SocialAccount.objects.update_or_create(
            platform="youtube",
            external_account_id=item["channel_id"],
            defaults={
                "display_name": item["title"],
                "is_active": True,
            },
        )
        youtube_accounts.append(account)

    google_business_accounts = []
    for item in google_business_locations:
        account, _ = SocialAccount.objects.update_or_create(
            platform="google_business",
            external_account_id=item["location_name"],
            defaults={
                "display_name": item["title"],
                "is_active": True,
            },
        )
        google_business_accounts.append(account)

    if discovery_errors:
        credential.last_error = " | ".join(discovery_errors)[:2000]
        credential.last_sync_status = "connected"
        credential.save(update_fields=["last_error", "last_sync_status", "updated_at"])

    return {
        "ga4_count": len(ga4_properties),
        "gsc_count": len(gsc_sites),
        "youtube_count": len(youtube_accounts),
        "google_business_count": len(google_business_accounts),
        "property_count": len({prop.pk for prop in updated_properties if prop.pk}),
        "errors": discovery_errors,
    }
