from __future__ import annotations

from datetime import date, datetime
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.utils import timezone

from .errors import MarketingServiceError


TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
TIKTOK_VIDEO_PAGE_SIZE = 20
TIKTOK_VIDEO_MAX_PAGES = 5
logger = logging.getLogger(__name__)


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
    logger.info("TikTok API request endpoint=%s method=%s", url, "POST" if body is not None else "GET")
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
        logger.error("TikTok API error endpoint=%s status=%s payload=%s", url, exc.code, detail[:2000])
        raise MarketingServiceError(f"TikTok API error {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        logger.error("TikTok API request failed endpoint=%s reason=%s", url, exc.reason)
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
    fields = (
        "id,title,create_time,share_url,cover_image_url,duration,"
        "view_count,like_count,comment_count,share_count"
    )
    rows: list[dict] = []
    cursor = None
    for _page in range(TIKTOK_VIDEO_MAX_PAGES):
        body = {"max_count": TIKTOK_VIDEO_PAGE_SIZE}
        if cursor is not None:
            body["cursor"] = cursor
        payload = _request_json(
            "/video/list/",
            access_token=access_token,
            params={"fields": fields},
            body=body,
        )
        data = payload.get("data") or {}
        for item in data.get("videos") or []:
            video_id = item.get("id")
            if not video_id:
                continue
            created = _as_int(item.get("create_time"))
            published_at = datetime.fromtimestamp(created, tz=timezone.get_current_timezone()) if created else None
            title = item.get("title") or video_id or "TikTok video"
            rows.append(
                {
                    "external_content_id": video_id,
                    "content_type": "video",
                    "title": title[:300],
                    "message_text": item.get("title") or "",
                    "permalink": item.get("share_url") or "",
                    "published_at": published_at,
                    "metric_payload": {
                        "date": end_date,
                        "views": _as_int(item.get("view_count")),
                        "likes": _as_int(item.get("like_count")),
                        "comments": _as_int(item.get("comment_count")),
                        "shares": _as_int(item.get("share_count")),
                    },
                }
            )
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
        if cursor in (None, ""):
            break
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
            "views": 0,
            "engagement_total": _as_int(profile.get("likes_count")),
        }
    ]


def fetch_tiktok_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok account id")
    return []
