from __future__ import annotations

from datetime import date, datetime
import json
import urllib.error
import urllib.parse
import urllib.request

from django.utils import timezone

from .errors import MarketingServiceError


TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


def _as_int(value) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _request_json(path: str, *, access_token: str, params: dict | None = None, body: dict | None = None) -> dict:
    url = path if path.startswith("http") else f"{TIKTOK_API_BASE}{path if path.startswith('/') else '/' + path}"
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"TikTok API error {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise MarketingServiceError(f"TikTok API request failed: {exc.reason}") from exc
    payload = json.loads(raw)
    error = payload.get("error") or {}
    if error and error.get("code") not in ("", "ok"):
        raise MarketingServiceError(f"TikTok API error: {error.get('code')} {error.get('message')}")
    return payload


def fetch_tiktok_profile(*, access_token: str) -> dict:
    if not access_token:
        raise MarketingServiceError("Missing TikTok access token")
    fields = ",".join(
        [
            "open_id",
            "union_id",
            "display_name",
            "username",
            "profile_deep_link",
            "follower_count",
            "following_count",
            "likes_count",
            "video_count",
        ]
    )
    payload = _request_json("/user/info/", access_token=access_token, params={"fields": fields})
    user = (payload.get("data") or {}).get("user") or {}
    if not user.get("open_id"):
        raise MarketingServiceError("TikTok profile response did not include open_id.")
    return user


def fetch_tiktok_content(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok credentials")
    fields = "id,title,create_time,share_url,view_count,like_count,comment_count,share_count"
    payload = _request_json(
        "/video/list/",
        access_token=access_token,
        params={"fields": fields},
        body={"max_count": 20},
    )
    rows: list[dict] = []
    for item in (payload.get("data") or {}).get("videos") or []:
        created = _as_int(item.get("create_time"))
        published_at = datetime.fromtimestamp(created, tz=timezone.get_current_timezone()) if created else None
        if published_at:
            published_date = published_at.date()
            if published_date < start_date or published_date > end_date:
                continue
        metric_date = published_at.date() if published_at else end_date
        rows.append(
            {
                "external_content_id": item.get("id"),
                "content_type": "video",
                "title": item.get("title") or item.get("id") or "TikTok video",
                "message_text": item.get("title") or "",
                "permalink": item.get("share_url") or "",
                "published_at": published_at,
                "metric_payload": {
                    "date": metric_date,
                    "views": _as_int(item.get("view_count")),
                    "likes": _as_int(item.get("like_count")),
                    "comments": _as_int(item.get("comment_count")),
                    "shares": _as_int(item.get("share_count")),
                },
            }
        )
    return rows


def fetch_tiktok_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing TikTok content id")
    return []


def fetch_tiktok_account_metrics(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok account id")
    profile = fetch_tiktok_profile(access_token=access_token)
    return [
        {
            "date": end_date,
            "followers_total": _as_int(profile.get("follower_count")),
            "views": _as_int(profile.get("video_count")),
            "engagement_total": _as_int(profile.get("likes_count")),
        }
    ]


def fetch_tiktok_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok account id")
    return []
