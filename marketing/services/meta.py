from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.utils import timezone

from .errors import MarketingServiceError


GRAPH_BASE = "https://graph.facebook.com/v20.0"


def _as_int(value) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _request_json(path_or_url: str, *, access_token: str, params: dict | None = None) -> dict:
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
        url = f"{GRAPH_BASE}{path}"

    query = dict(params or {})
    if access_token and "access_token" not in query:
        query["access_token"] = access_token
    if query:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(query, doseq=True)}"

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"Meta API error {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise MarketingServiceError(f"Meta API request failed: {exc.reason}") from exc
    return json.loads(raw)


def _iter_graph_data(path_or_url: str, *, access_token: str, params: dict | None = None, limit_pages: int = 10):
    url = path_or_url
    page_count = 0
    while url and page_count < limit_pages:
        payload = _request_json(url, access_token=access_token, params=params if page_count == 0 else None)
        for row in payload.get("data") or []:
            yield row
        url = payload.get("paging", {}).get("next")
        page_count += 1


def _date_to_unix(value: date, *, end_of_day: bool = False) -> int:
    wall_time = time.max if end_of_day else time.min
    dt = datetime.combine(value, wall_time)
    aware = timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt
    return int(aware.timestamp())


def _parse_meta_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _insight_series(payload: dict) -> dict[date, dict[str, int]]:
    rows: dict[date, dict[str, int]] = {}
    for metric in payload.get("data") or []:
        name = metric.get("name") or ""
        for value_row in metric.get("values") or []:
            raw_date = (value_row.get("end_time") or value_row.get("date") or "")[:10]
            try:
                metric_date = date.fromisoformat(raw_date)
            except ValueError:
                metric_date = timezone.localdate()
            value = value_row.get("value")
            if isinstance(value, dict):
                amount = sum(_as_int(item) for item in value.values())
            else:
                amount = _as_int(value)
            rows.setdefault(metric_date, {})[name] = amount
    return rows


def _metric_value(payload: dict, metric_name: str) -> int:
    rows = _insight_series(payload)
    total = 0
    for values in rows.values():
        total += values.get(metric_name, 0)
    return total


def _content_type_for_meta(platform: str, media_type: str = "") -> str:
    media_type = (media_type or "").upper()
    if platform == "instagram" and media_type == "VIDEO":
        return "reel"
    if platform == "instagram":
        return "post"
    return "post"


def fetch_meta_content(
    *,
    access_token: str,
    account_id: str,
    start_date: date,
    end_date: date,
    platform: str = "facebook",
) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Meta credentials")

    rows: list[dict] = []
    if platform == "instagram":
        fields = "id,caption,media_type,permalink,timestamp,like_count,comments_count"
        params = {"fields": fields, "limit": 50}
        for item in _iter_graph_data(f"/{account_id}/media", access_token=access_token, params=params):
            published_at = _parse_meta_datetime(item.get("timestamp") or "")
            if published_at:
                published_date = timezone.localtime(published_at).date()
                if published_date < start_date or published_date > end_date:
                    continue
            metric_payload = {
                "date": published_at.date() if published_at else end_date,
                "likes": _as_int(item.get("like_count")),
                "comments": _as_int(item.get("comments_count")),
            }
            rows.append(
                {
                    "external_content_id": item.get("id"),
                    "content_type": _content_type_for_meta("instagram", item.get("media_type") or ""),
                    "title": (item.get("caption") or "")[:300],
                    "message_text": item.get("caption") or "",
                    "permalink": item.get("permalink") or "",
                    "published_at": published_at,
                    "metric_payload": metric_payload,
                }
            )
        return rows

    fields = "id,message,permalink_url,created_time,shares,comments.summary(true),likes.summary(true)"
    params = {
        "fields": fields,
        "since": _date_to_unix(start_date),
        "until": _date_to_unix(end_date, end_of_day=True),
        "limit": 50,
    }
    for item in _iter_graph_data(f"/{account_id}/posts", access_token=access_token, params=params):
        published_at = _parse_meta_datetime(item.get("created_time") or "")
        likes = _as_int((item.get("likes") or {}).get("summary", {}).get("total_count"))
        comments = _as_int((item.get("comments") or {}).get("summary", {}).get("total_count"))
        shares = _as_int((item.get("shares") or {}).get("count"))
        rows.append(
            {
                "external_content_id": item.get("id"),
                "content_type": "post",
                "title": (item.get("message") or "")[:300],
                "message_text": item.get("message") or "",
                "permalink": item.get("permalink_url") or "",
                "published_at": published_at,
                "metric_payload": {
                    "date": published_at.date() if published_at else end_date,
                    "likes": likes,
                    "comments": comments,
                    "shares": shares,
                },
            }
        )
    return rows


def fetch_meta_metrics(
    *,
    access_token: str,
    content_id: str,
    start_date: date,
    end_date: date,
    platform: str = "facebook",
) -> list[dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing Meta content id")

    if platform == "instagram":
        metric_names = "impressions,reach,saved,shares,total_interactions"
        payload = _request_json(f"/{content_id}/insights", access_token=access_token, params={"metric": metric_names})
        return [
            {
                "date": end_date,
                "impressions": _metric_value(payload, "impressions"),
                "reach": _metric_value(payload, "reach"),
                "saves": _metric_value(payload, "saved"),
                "shares": _metric_value(payload, "shares"),
            }
        ]

    metric_names = "post_impressions,post_impressions_unique,post_clicks,post_engaged_users"
    payload = _request_json(f"/{content_id}/insights", access_token=access_token, params={"metric": metric_names})
    return [
        {
            "date": end_date,
            "impressions": _metric_value(payload, "post_impressions"),
            "reach": _metric_value(payload, "post_impressions_unique"),
            "clicks": _metric_value(payload, "post_clicks"),
            "profile_visits": _metric_value(payload, "post_engaged_users"),
        }
    ]


def fetch_meta_account_metrics(
    *,
    access_token: str,
    account_id: str,
    start_date: date,
    end_date: date,
    platform: str = "facebook",
) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Meta account id")

    if platform == "instagram":
        profile = _request_json(
            f"/{account_id}",
            access_token=access_token,
            params={"fields": "followers_count,media_count,username,name"},
        )
        metric_names = "impressions,reach,profile_views"
        try:
            payload = _request_json(
                f"/{account_id}/insights",
                access_token=access_token,
                params={"metric": metric_names, "period": "day", "since": start_date.isoformat(), "until": end_date.isoformat()},
            )
            daily = _insight_series(payload)
        except MarketingServiceError:
            daily = {}
        if not daily:
            daily = {end_date: {}}
        return [
            {
                "date": metric_date,
                "followers_total": _as_int(profile.get("followers_count")),
                "impressions": values.get("impressions", 0),
                "reach": values.get("reach", 0),
                "views": values.get("profile_views", 0),
            }
            for metric_date, values in sorted(daily.items())
        ]

    profile = _request_json(f"/{account_id}", access_token=access_token, params={"fields": "followers_count,fan_count"})
    metric_names = "page_impressions,page_impressions_unique,page_post_engagements,page_consumptions"
    try:
        payload = _request_json(
            f"/{account_id}/insights",
            access_token=access_token,
            params={"metric": metric_names, "period": "day", "since": start_date.isoformat(), "until": end_date.isoformat()},
        )
        daily = _insight_series(payload)
    except MarketingServiceError:
        daily = {}
    if not daily:
        daily = {end_date: {}}
    followers = _as_int(profile.get("followers_count") or profile.get("fan_count"))
    return [
        {
            "date": metric_date,
            "followers_total": followers,
            "impressions": values.get("page_impressions", 0),
            "reach": values.get("page_impressions_unique", 0),
            "clicks": values.get("page_consumptions", 0),
            "engagement_total": values.get("page_post_engagements", 0),
        }
        for metric_date, values in sorted(daily.items())
    ]


def fetch_meta_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Meta account id")
    return []


def fetch_meta_ad_accounts(*, access_token: str) -> list[dict]:
    if not access_token:
        raise MarketingServiceError("Missing Meta access token")
    rows: list[dict] = []
    params = {"fields": "id,account_id,name,currency,account_status", "limit": 100}
    for item in _iter_graph_data("/me/adaccounts", access_token=access_token, params=params):
        rows.append(
            {
                "external_ad_account_id": item.get("id") or f"act_{item.get('account_id')}",
                "account_id": item.get("account_id") or "",
                "name": item.get("name") or item.get("id") or "Meta Ad Account",
                "currency": item.get("currency") or "",
                "is_active": str(item.get("account_status") or "") in {"", "1"},
            }
        )
    return rows


def fetch_meta_ad_campaigns(*, access_token: str, ad_account_id: str) -> list[dict]:
    if not access_token or not ad_account_id:
        raise MarketingServiceError("Missing Meta ad account id")
    rows: list[dict] = []
    params = {"fields": "id,name,status,objective", "limit": 100}
    for item in _iter_graph_data(f"/{ad_account_id}/campaigns", access_token=access_token, params=params):
        rows.append(
            {
                "external_campaign_id": item.get("id"),
                "name": item.get("name") or item.get("id") or "Meta Campaign",
                "status": item.get("status") or "",
                "objective": item.get("objective") or "",
            }
        )
    return rows


def _conversion_count(actions: list[dict[str, Any]]) -> int:
    conversion_markers = ("lead", "purchase", "complete_registration", "submit_application", "contact")
    total = 0
    for action in actions or []:
        action_type = (action.get("action_type") or "").lower()
        if any(marker in action_type for marker in conversion_markers):
            total += _as_int(action.get("value"))
    return total


def fetch_meta_ad_insights(*, access_token: str, ad_account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not ad_account_id:
        raise MarketingServiceError("Missing Meta ad account id")
    rows: list[dict] = []
    params = {
        "fields": "campaign_id,campaign_name,spend,impressions,reach,clicks,ctr,cpc,cpm,actions",
        "level": "campaign",
        "time_increment": 1,
        "time_range": json.dumps({"since": start_date.isoformat(), "until": end_date.isoformat()}),
        "limit": 200,
    }
    for item in _iter_graph_data(f"/{ad_account_id}/insights", access_token=access_token, params=params):
        try:
            metric_date = date.fromisoformat(item.get("date_start") or end_date.isoformat())
        except ValueError:
            metric_date = end_date
        conversions = _conversion_count(item.get("actions") or [])
        rows.append(
            {
                "external_campaign_id": item.get("campaign_id") or "unknown",
                "campaign_name": item.get("campaign_name") or item.get("campaign_id") or "Meta Campaign",
                "date": metric_date,
                "spend": _as_decimal(item.get("spend")),
                "impressions": _as_int(item.get("impressions")),
                "reach": _as_int(item.get("reach")),
                "clicks": _as_int(item.get("clicks")),
                "ctr": _as_decimal(item.get("ctr")),
                "cpc": _as_decimal(item.get("cpc")),
                "cpm": _as_decimal(item.get("cpm")),
                "conversions": conversions,
                "cost_per_conversion": (_as_decimal(item.get("spend")) / conversions) if conversions else Decimal("0"),
            }
        )
    return rows
