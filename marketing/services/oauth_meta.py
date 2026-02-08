import json
import urllib.error
import urllib.parse
import urllib.request

from marketing.services.errors import MarketingServiceError

GRAPH_BASE = "https://graph.facebook.com/v20.0"
DIALOG_BASE = "https://www.facebook.com/v20.0/dialog/oauth"


def build_meta_oauth_url(*, app_id: str, redirect_uri: str, state: str, scopes: list[str]) -> str:
    query = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "scope": ",".join(scopes),
    }
    return f"{DIALOG_BASE}?{urllib.parse.urlencode(query)}"


def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise MarketingServiceError(f"Meta API error: {body}") from exc
    except Exception as exc:
        raise MarketingServiceError(str(exc)) from exc


def exchange_code_for_token(*, app_id: str, app_secret: str, redirect_uri: str, code: str) -> dict:
    query = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    url = f"{GRAPH_BASE}/oauth/access_token?{urllib.parse.urlencode(query)}"
    return _fetch_json(url)


def exchange_long_lived_token(*, app_id: str, app_secret: str, access_token: str) -> dict:
    query = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": access_token,
    }
    url = f"{GRAPH_BASE}/oauth/access_token?{urllib.parse.urlencode(query)}"
    return _fetch_json(url)


def fetch_meta_pages(*, access_token: str) -> list[dict]:
    pages: list[dict] = []
    url = f"{GRAPH_BASE}/me/accounts?fields=id,name,instagram_business_account,timezone&access_token={urllib.parse.quote(access_token)}"
    while url:
        payload = _fetch_json(url)
        pages.extend(payload.get("data") or [])
        url = payload.get("paging", {}).get("next")
    return pages
