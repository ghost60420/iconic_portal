from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from marketing.services.errors import MarketingServiceError

GRAPH_BASE = "https://graph.facebook.com/v20.0"
INSTAGRAM_AUTH_BASE = "https://www.instagram.com/oauth/authorize"


def instagram_oauth_configured() -> bool:
    return bool(
        settings.MARKETING_INSTAGRAM_APP_ID
        and settings.MARKETING_INSTAGRAM_APP_SECRET
        and settings.MARKETING_INSTAGRAM_REDIRECT_URI
    )


def build_instagram_oauth_url(*, state: str) -> str:
    if not instagram_oauth_configured():
        raise MarketingServiceError("Instagram OAuth is not configured.")
    query = {
        "force_reauth": "true",
        "client_id": settings.MARKETING_INSTAGRAM_APP_ID,
        "redirect_uri": settings.MARKETING_INSTAGRAM_REDIRECT_URI,
        "response_type": "code",
        "scope": ",".join(settings.MARKETING_INSTAGRAM_SCOPES),
        "state": state,
    }
    return f"{INSTAGRAM_AUTH_BASE}?{urllib.parse.urlencode(query)}"


def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"Instagram API error: {body[:500]}") from exc
    except Exception as exc:
        raise MarketingServiceError(str(exc)) from exc


def exchange_instagram_code_for_token(*, code: str) -> dict:
    query = {
        "client_id": settings.MARKETING_INSTAGRAM_APP_ID,
        "client_secret": settings.MARKETING_INSTAGRAM_APP_SECRET,
        "redirect_uri": settings.MARKETING_INSTAGRAM_REDIRECT_URI,
        "code": code,
    }
    url = f"{GRAPH_BASE}/oauth/access_token?{urllib.parse.urlencode(query)}"
    return _fetch_json(url)


def exchange_instagram_long_lived_token(*, access_token: str) -> dict:
    query = {
        "grant_type": "fb_exchange_token",
        "client_id": settings.MARKETING_INSTAGRAM_APP_ID,
        "client_secret": settings.MARKETING_INSTAGRAM_APP_SECRET,
        "fb_exchange_token": access_token,
    }
    url = f"{GRAPH_BASE}/oauth/access_token?{urllib.parse.urlencode(query)}"
    return _fetch_json(url)


def fetch_instagram_permissions(*, access_token: str) -> dict:
    payload = _fetch_json(f"{GRAPH_BASE}/me/permissions?access_token={urllib.parse.quote(access_token)}")
    granted = []
    declined = []
    for item in payload.get("data") or []:
        permission = item.get("permission") or ""
        status = item.get("status") or ""
        if not permission:
            continue
        if status == "granted":
            granted.append(permission)
        else:
            declined.append(permission)
    return {
        "granted": sorted(set(granted)),
        "declined": sorted(set(declined)),
    }


def fetch_instagram_business_accounts(*, access_token: str) -> list[dict]:
    fields = ",".join(
        [
            "id",
            "name",
            "access_token",
            "instagram_business_account{id,username,name,followers_count,media_count}",
            "timezone",
        ]
    )
    url = f"{GRAPH_BASE}/me/accounts?fields={urllib.parse.quote(fields)}&access_token={urllib.parse.quote(access_token)}"
    accounts: list[dict] = []
    while url:
        payload = _fetch_json(url)
        for page in payload.get("data") or []:
            ig_info = page.get("instagram_business_account") or {}
            ig_id = ig_info.get("id")
            if not ig_id:
                continue
            accounts.append(
                {
                    "id": ig_id,
                    "username": ig_info.get("username") or "",
                    "name": ig_info.get("name") or "",
                    "followers_count": ig_info.get("followers_count"),
                    "media_count": ig_info.get("media_count"),
                    "page_id": page.get("id") or "",
                    "page_name": page.get("name") or "",
                    "page_access_token": page.get("access_token") or "",
                    "timezone": page.get("timezone") or "",
                }
            )
        url = payload.get("paging", {}).get("next")
    return accounts
