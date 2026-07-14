from datetime import date, datetime
from hashlib import sha256
from typing import List, Dict
from urllib.parse import urlencode

from django.utils import timezone

from marketing.services.google_oauth import google_api_request_json
from .errors import MarketingServiceError


GBP_PERFORMANCE_URL = "https://businessprofileperformance.googleapis.com/v1"
GBP_MYBUSINESS_URL = "https://mybusiness.googleapis.com/v4"
GBP_DAILY_METRICS = [
    "WEBSITE_CLICKS",
    "CALL_CLICKS",
    "BUSINESS_DIRECTION_REQUESTS",
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
]
GBP_IMPRESSION_METRICS = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
}


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _location_resource(account_id: str) -> str:
    cleaned = (account_id or "").strip().strip("/")
    if not cleaned:
        return ""
    if cleaned.startswith("locations/"):
        return cleaned
    if "/locations/" in cleaned:
        return f"locations/{cleaned.split('/locations/', 1)[1]}"
    return f"locations/{cleaned}"


def _resource_id(resource_name: str, marker: str) -> str:
    cleaned = (resource_name or "").strip().strip("/")
    if not cleaned:
        return ""
    if cleaned.startswith(marker):
        return cleaned.replace(marker, "", 1).split("/", 1)[0]
    if f"/{marker}" in cleaned:
        return cleaned.split(f"/{marker}", 1)[1].split("/", 1)[0]
    return cleaned.split("/", 1)[0]


def _v4_location_parent(account_id: str, business_account_name: str = "") -> str:
    account_resource = (business_account_name or "").strip().strip("/")
    account_resource_id = _resource_id(account_resource, "accounts/")
    location_resource_id = _resource_id(_location_resource(account_id), "locations/")
    if not account_resource_id:
        raise MarketingServiceError("Missing Google Business account resource for location.")
    if not location_resource_id:
        raise MarketingServiceError("Missing Google Business location id.")
    return f"accounts/{account_resource_id}/locations/{location_resource_id}"


def _google_date(value: dict, fallback: date) -> date:
    if not isinstance(value, dict):
        return fallback
    try:
        return date(int(value.get("year")), int(value.get("month")), int(value.get("day")))
    except (TypeError, ValueError):
        return fallback


def _parse_google_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def _stable_content_id(kind: str, resource_name: str) -> str:
    raw = f"gbp-{kind}:{resource_name or kind}"
    if len(raw) <= 120:
        return raw
    return f"gbp-{kind}:{sha256(raw.encode('utf-8')).hexdigest()}"


def _paged_google_business_rows(*, url: str, collection_key: str, access_token: str, page_size: int) -> list[dict]:
    rows = []
    page_token = ""
    while True:
        params = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        payload = google_api_request_json(f"{url}?{urlencode(params)}", access_token=access_token)
        rows.extend(payload.get(collection_key, []) or [])
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    return rows


def _normalize_local_post(post: dict) -> dict:
    name = post.get("name") or post.get("localPostName") or ""
    topic_type = (post.get("topicType") or "STANDARD").replace("_", " ").title()
    summary = post.get("summary") or ""
    return {
        "external_content_id": _stable_content_id("post", name),
        "content_type": "post",
        "title": f"Google Business Post - {topic_type}",
        "message_text": summary,
        "permalink": post.get("searchUrl") or "",
        "published_at": _parse_google_datetime(post.get("createTime") or post.get("updateTime") or ""),
    }


def _normalize_review(review: dict) -> dict:
    name = review.get("name") or review.get("reviewId") or ""
    reviewer = review.get("reviewer") or {}
    reviewer_name = reviewer.get("displayName") or "Google reviewer"
    rating = (review.get("starRating") or "Rating").replace("_", " ").title()
    comment = review.get("comment") or ""
    reply = review.get("reviewReply") or {}
    reply_comment = reply.get("comment") or ""
    message_parts = [part for part in (comment, f"Owner reply: {reply_comment}" if reply_comment else "") if part]
    return {
        "external_content_id": _stable_content_id("review", name),
        "content_type": "post",
        "title": f"Google Review - {rating} - {reviewer_name}",
        "message_text": "\n\n".join(message_parts),
        "permalink": "",
        "published_at": _parse_google_datetime(review.get("createTime") or review.get("updateTime") or ""),
    }


def fetch_google_business_content(
    *,
    access_token: str,
    account_id: str,
    start_date: date,
    end_date: date,
    business_account_name: str = "",
) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Google Business credentials")
    parent = _v4_location_parent(account_id, business_account_name)
    post_rows = _paged_google_business_rows(
        url=f"{GBP_MYBUSINESS_URL}/{parent}/localPosts",
        collection_key="localPosts",
        access_token=access_token,
        page_size=100,
    )
    review_rows = _paged_google_business_rows(
        url=f"{GBP_MYBUSINESS_URL}/{parent}/reviews",
        collection_key="reviews",
        access_token=access_token,
        page_size=50,
    )
    return [_normalize_local_post(row) for row in post_rows] + [_normalize_review(row) for row in review_rows]


def fetch_google_business_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing Google Business content id")
    return []


def fetch_google_business_account_metrics(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Google Business account id")
    location = _location_resource(account_id)
    if not location:
        raise MarketingServiceError("Missing Google Business location id")

    params = {
        "dailyMetrics": GBP_DAILY_METRICS,
        "dailyRange.start_date.year": start_date.year,
        "dailyRange.start_date.month": start_date.month,
        "dailyRange.start_date.day": start_date.day,
        "dailyRange.end_date.year": end_date.year,
        "dailyRange.end_date.month": end_date.month,
        "dailyRange.end_date.day": end_date.day,
    }
    url = f"{GBP_PERFORMANCE_URL}/{location}:fetchMultiDailyMetricsTimeSeries?{urlencode(params, doseq=True)}"
    payload = google_api_request_json(url, access_token=access_token)
    rows_by_date = {}

    for multi_series in payload.get("multiDailyMetricTimeSeries", []):
        for series in multi_series.get("dailyMetricTimeSeries", []):
            metric = series.get("dailyMetric") or ""
            points = ((series.get("timeSeries") or {}).get("datedValues")) or []
            for point in points:
                metric_date = _google_date(point.get("date") or {}, end_date)
                value = _to_int(point.get("value"))
                row = rows_by_date.setdefault(
                    metric_date,
                    {
                        "date": metric_date,
                        "impressions": 0,
                        "reach": 0,
                        "clicks": 0,
                        "engagement_total": 0,
                    },
                )
                if metric == "WEBSITE_CLICKS":
                    row["clicks"] += value
                elif metric == "CALL_CLICKS":
                    row["engagement_total"] += value
                elif metric == "BUSINESS_DIRECTION_REQUESTS":
                    row["reach"] += value
                elif metric in GBP_IMPRESSION_METRICS:
                    row["impressions"] += value

    return [rows_by_date[key] for key in sorted(rows_by_date)]


def fetch_google_business_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Google Business account id")
    return []
