from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from marketing.services.errors import MarketingServiceError

GRAPH_BASE = "https://graph.facebook.com/v20.0"
INSTAGRAM_AUTH_BASE = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_ENDPOINT = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com"


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


def _post_form_json(url: str, data: dict[str, str]) -> dict:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"Instagram API error: {body[:500]}") from exc
    except Exception as exc:
        raise MarketingServiceError(str(exc)) from exc


def exchange_instagram_code_for_token(*, code: str) -> dict:
    data = {
        "client_id": settings.MARKETING_INSTAGRAM_APP_ID,
        "client_secret": settings.MARKETING_INSTAGRAM_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": settings.MARKETING_INSTAGRAM_REDIRECT_URI,
        "code": code,
    }
    return _post_form_json(INSTAGRAM_TOKEN_ENDPOINT, data)


def exchange_instagram_long_lived_token(*, access_token: str) -> dict:
    query = {
        "grant_type": "ig_exchange_token",
        "client_secret": settings.MARKETING_INSTAGRAM_APP_SECRET,
        "access_token": access_token,
    }
    url = f"{INSTAGRAM_GRAPH_BASE}/access_token?{urllib.parse.urlencode(query)}"
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
            "user_id",
            "username",
            "account_type",
            "media_count",
            "followers_count",
        ]
    )
    url = f"{INSTAGRAM_GRAPH_BASE}/me?fields={urllib.parse.quote(fields)}&access_token={urllib.parse.quote(access_token)}"
    payload = _fetch_json(url)
    account_id = payload.get("user_id") or payload.get("id")
    if not account_id:
        return []
    return [
        {
            "id": account_id,
            "username": payload.get("username") or "",
            "name": payload.get("username") or "",
            "account_type": payload.get("account_type") or "",
            "followers_count": payload.get("followers_count"),
            "media_count": payload.get("media_count"),
        }
    ]
