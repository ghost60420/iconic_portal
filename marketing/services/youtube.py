from datetime import date, timedelta, timezone as dt_timezone
from typing import List, Dict
from urllib.parse import urlencode

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from marketing.services.google_oauth import google_api_request_json
from .errors import MarketingServiceError


YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_provider_datetime(value: str):
    parsed = parse_datetime(value or "")
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _channel_query(channel_id: str) -> str:
    params = {"part": "snippet,statistics", "maxResults": "1"}
    if channel_id == "mine":
        params["mine"] = "true"
    else:
        params["id"] = channel_id
    return f"{YOUTUBE_CHANNELS_URL}?{urlencode(params)}"


def _resolve_channel_id(*, access_token: str, channel_id: str) -> str:
    if channel_id != "mine":
        return channel_id
    payload = google_api_request_json(_channel_query(channel_id), access_token=access_token)
    items = payload.get("items", [])
    if not items:
        raise MarketingServiceError("No YouTube channel found for this Google account.")
    return items[0].get("id") or ""


def fetch_youtube_content(*, access_token: str, channel_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not channel_id:
        raise MarketingServiceError("Missing YouTube credentials")
    resolved_channel_id = _resolve_channel_id(access_token=access_token, channel_id=channel_id)
    if not resolved_channel_id:
        raise MarketingServiceError("Missing YouTube channel id")

    params = {
        "part": "snippet",
        "channelId": resolved_channel_id,
        "type": "video",
        "order": "date",
        "maxResults": "25",
        "publishedAfter": f"{start_date.isoformat()}T00:00:00Z",
        "publishedBefore": f"{(end_date + timedelta(days=1)).isoformat()}T00:00:00Z",
    }
    payload = google_api_request_json(f"{YOUTUBE_SEARCH_URL}?{urlencode(params)}", access_token=access_token)
    rows = []
    for item in payload.get("items", []):
        video_id = (item.get("id") or {}).get("videoId") or ""
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        rows.append(
            {
                "external_content_id": video_id,
                "content_type": "video",
                "title": snippet.get("title") or "",
                "message_text": snippet.get("description") or "",
                "permalink": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": _parse_provider_datetime(snippet.get("publishedAt") or ""),
            }
        )
    return rows


def fetch_youtube_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing YouTube content id")
    params = {
        "part": "statistics",
        "id": content_id,
        "maxResults": "1",
    }
    payload = google_api_request_json(f"{YOUTUBE_VIDEOS_URL}?{urlencode(params)}", access_token=access_token)
    items = payload.get("items", [])
    if not items:
        return []
    stats = items[0].get("statistics") or {}
    return [
        {
            "date": end_date,
            "views": _to_int(stats.get("viewCount")),
            "likes": _to_int(stats.get("likeCount")),
            "comments": _to_int(stats.get("commentCount")),
        }
    ]


def fetch_youtube_account_metrics(*, access_token: str, channel_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not channel_id:
        raise MarketingServiceError("Missing YouTube channel id")
    payload = google_api_request_json(_channel_query(channel_id), access_token=access_token)
    items = payload.get("items", [])
    if not items:
        return []
    stats = items[0].get("statistics") or {}
    return [
        {
            "date": end_date,
            "followers_total": _to_int(stats.get("subscriberCount")),
            "views": _to_int(stats.get("viewCount")),
            "engagement_total": _to_int(stats.get("videoCount")),
        }
    ]


def fetch_youtube_audience(*, access_token: str, channel_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not channel_id:
        raise MarketingServiceError("Missing YouTube channel id")
    return []
