from __future__ import annotations

from datetime import date, datetime, time
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings
from django.utils import timezone

from .errors import MarketingServiceError


LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"


def _as_int(value) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "LinkedIn-Version": getattr(settings, "MARKETING_LINKEDIN_VERSION", "202606"),
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _request_json(path: str, *, access_token: str, params: dict | None = None) -> dict:
    url = path if path.startswith("http") else f"{LINKEDIN_REST_BASE}{path if path.startswith('/') else '/' + path}"
    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params, safe='(),:')}"
    request = urllib.request.Request(url, headers=_headers(access_token), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise MarketingServiceError(f"LinkedIn API error {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise MarketingServiceError(f"LinkedIn API request failed: {exc.reason}") from exc
    return json.loads(raw)


def _date_to_millis(value: date, *, end_of_day: bool = False) -> int:
    wall_time = time.max if end_of_day else time.min
    dt = datetime.combine(value, wall_time)
    aware = timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt
    return int(aware.timestamp() * 1000)


def _organization_id(account_id: str) -> str:
    if account_id.startswith("urn:li:organization:"):
        return account_id.rsplit(":", 1)[-1]
    return account_id


def _organization_urn(account_id: str) -> str:
    return account_id if account_id.startswith("urn:li:organization:") else f"urn:li:organization:{account_id}"


def discover_linkedin_organizations(*, access_token: str) -> list[dict]:
    if not access_token:
        raise MarketingServiceError("Missing LinkedIn access token")
    payload = _request_json(
        "/organizationAcls",
        access_token=access_token,
        params={"q": "roleAssignee", "state": "APPROVED"},
    )
    organizations: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("elements") or []:
        urn = item.get("organization") or ""
        if not urn or urn in seen:
            continue
        seen.add(urn)
        org_id = _organization_id(urn)
        name = urn
        try:
            org_payload = _request_json(
                f"/organizations/{org_id}",
                access_token=access_token,
                params={"projection": "(id,localizedName,vanityName)"},
            )
            name = org_payload.get("localizedName") or org_payload.get("vanityName") or urn
        except MarketingServiceError:
            pass
        organizations.append({"id": org_id, "urn": urn, "name": name})
    return organizations


def fetch_linkedin_content(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn credentials")
    urn = _organization_urn(account_id)
    try:
        payload = _request_json("/posts", access_token=access_token, params={"q": "author", "author": urn, "count": 20})
    except MarketingServiceError:
        return []

    rows: list[dict] = []
    start_ms = _date_to_millis(start_date)
    end_ms = _date_to_millis(end_date, end_of_day=True)
    for item in payload.get("elements") or []:
        created_ms = _as_int((item.get("createdAt") or item.get("lastModifiedAt") or 0))
        if created_ms and (created_ms < start_ms or created_ms > end_ms):
            continue
        published_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.get_current_timezone()) if created_ms else None
        commentary = item.get("commentary") or item.get("content", {}).get("title") or ""
        rows.append(
            {
                "external_content_id": item.get("id") or item.get("urn"),
                "content_type": "post",
                "title": commentary[:300],
                "message_text": commentary,
                "permalink": "",
                "published_at": published_at,
            }
        )
    return rows


def _share_statistics(*, access_token: str, account_id: str, start_date: date, end_date: date, share_urn: str = "") -> list[dict]:
    urn = _organization_urn(account_id)
    params = {
        "q": "organizationalEntity",
        "organizationalEntity": urn,
        "timeIntervals": (
            f"(timeRange:(start:{_date_to_millis(start_date)},end:{_date_to_millis(end_date, end_of_day=True)}),"
            "timeGranularityType:DAY)"
        ),
    }
    if share_urn:
        params["shares"] = f"List({share_urn})"
    payload = _request_json("/organizationalEntityShareStatistics", access_token=access_token, params=params)
    return payload.get("elements") or []


def _total_share_stats(item: dict[str, Any]) -> dict:
    return item.get("totalShareStatistics") or {}


def fetch_linkedin_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing LinkedIn content id")
    # LinkedIn requires the organization for per-share stats; the daily command keeps this optional.
    return []


def fetch_linkedin_account_metrics(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn account id")
    rows: dict[date, dict] = {}

    try:
        follower_payload = _request_json(
            "/organizationalEntityFollowerStatistics",
            access_token=access_token,
            params={
                "q": "organizationalEntity",
                "organizationalEntity": _organization_urn(account_id),
                "timeIntervals": (
                    f"(timeRange:(start:{_date_to_millis(start_date)},end:{_date_to_millis(end_date, end_of_day=True)}),"
                    "timeGranularityType:DAY)"
                ),
            },
        )
        for item in follower_payload.get("elements") or []:
            raw_start = (item.get("timeRange") or {}).get("start")
            metric_date = datetime.fromtimestamp(raw_start / 1000, tz=timezone.get_current_timezone()).date() if raw_start else end_date
            gains = item.get("followerGains") or {}
            rows.setdefault(metric_date, {"date": metric_date})
            rows[metric_date]["followers_change"] = _as_int(gains.get("organicFollowerGain")) + _as_int(gains.get("paidFollowerGain"))
    except MarketingServiceError:
        pass

    for item in _share_statistics(access_token=access_token, account_id=account_id, start_date=start_date, end_date=end_date):
        raw_start = (item.get("timeRange") or {}).get("start")
        metric_date = datetime.fromtimestamp(raw_start / 1000, tz=timezone.get_current_timezone()).date() if raw_start else end_date
        stats = _total_share_stats(item)
        rows.setdefault(metric_date, {"date": metric_date})
        rows[metric_date].update(
            {
                "impressions": _as_int(stats.get("impressionCount")),
                "clicks": _as_int(stats.get("clickCount")),
                "engagement_total": (
                    _as_int(stats.get("likeCount"))
                    + _as_int(stats.get("commentCount"))
                    + _as_int(stats.get("shareCount"))
                    + _as_int(stats.get("clickCount"))
                ),
            }
        )

    if not rows:
        rows[end_date] = {"date": end_date}
    return [rows[key] for key in sorted(rows)]


def fetch_linkedin_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn account id")
    return []
