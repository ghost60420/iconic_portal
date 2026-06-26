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
LINKEDIN_PAGE_SIZE = 100


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
    if account_id.startswith("urn:li:organizationBrand:"):
        return account_id.rsplit(":", 1)[-1]
    return account_id


def _organization_urn(account_id: str) -> str:
    if account_id.startswith("urn:li:organizationBrand:"):
        return f"urn:li:organization:{_organization_id(account_id)}"
    return account_id if account_id.startswith("urn:li:organization:") else f"urn:li:organization:{account_id}"


def _organization_urn_from_acl(item: dict[str, Any]) -> str:
    return (
        item.get("organization")
        or item.get("organizationTarget")
        or item.get("organizationalTarget")
        or item.get("organizationalEntity")
        or ""
    )


def _localized_name(payload: dict[str, Any]) -> str:
    if payload.get("localizedName"):
        return str(payload["localizedName"])
    name = payload.get("name") or {}
    localized = name.get("localized") if isinstance(name, dict) else {}
    if isinstance(localized, dict):
        preferred = name.get("preferredLocale") or {}
        locale_key = "_".join(
            item for item in [preferred.get("language"), preferred.get("country")] if item
        )
        if locale_key and localized.get(locale_key):
            return str(localized[locale_key])
        for value in localized.values():
            if value:
                return str(value)
    return ""


def _page_url_from_org(payload: dict[str, Any]) -> str:
    vanity_name = payload.get("vanityName") or ""
    if vanity_name:
        return f"https://www.linkedin.com/company/{vanity_name}/"
    return payload.get("localizedWebsite") or ""


def _date_from_millis(value, fallback: date) -> date:
    millis = _as_int(value)
    if not millis:
        return fallback
    return datetime.fromtimestamp(millis / 1000, tz=timezone.get_current_timezone()).date()


def _row_for(rows: dict[date, dict], metric_date: date) -> dict:
    return rows.setdefault(
        metric_date,
        {
            "date": metric_date,
            "followers_total": 0,
            "followers_change": 0,
            "impressions": 0,
            "reach": 0,
            "views": 0,
            "clicks": 0,
            "engagement_total": 0,
        },
    )


def _nested_int_total(value, *, key_hints: tuple[str, ...] = ()) -> int:
    if isinstance(value, dict):
        total = 0
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, (dict, list)):
                total += _nested_int_total(item, key_hints=key_hints)
            elif not key_hints or any(hint in key_text for hint in key_hints):
                total += _as_int(item)
        return total
    if isinstance(value, list):
        return sum(_nested_int_total(item, key_hints=key_hints) for item in value)
    return _as_int(value) if not key_hints else 0


def _page_views_total(total: dict[str, Any]) -> int:
    views = total.get("views") or {}
    for key in ("allPageViews", "overviewPageViews"):
        bucket = views.get(key) or {}
        count = _as_int(bucket.get("pageViews"))
        if count:
            return count
    return _nested_int_total(views, key_hints=("pageviews",))


def _share_metric_row(item: dict[str, Any], *, fallback_date: date) -> dict:
    stats = _total_share_stats(item)
    metric_date = _date_from_millis((item.get("timeRange") or {}).get("start"), fallback_date)
    likes = _as_int(stats.get("likeCount"))
    comments = _as_int(stats.get("commentCount"))
    shares = _as_int(stats.get("shareCount"))
    clicks = _as_int(stats.get("clickCount"))
    impressions = _as_int(stats.get("impressionCount"))
    reach = _as_int(stats.get("uniqueImpressionsCount"))
    return {
        "date": metric_date,
        "impressions": impressions,
        "reach": reach,
        "views": impressions,
        "clicks": clicks,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "saves": 0,
    }


def discover_linkedin_organizations(*, access_token: str) -> list[dict]:
    if not access_token:
        raise MarketingServiceError("Missing LinkedIn access token")
    organizations: list[dict] = []
    seen: set[str] = set()
    start = 0
    while True:
        payload = _request_json(
            "/organizationAcls",
            access_token=access_token,
            params={
                "q": "roleAssignee",
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
                "count": LINKEDIN_PAGE_SIZE,
                "start": start,
            },
        )
        elements = payload.get("elements") or []
        for item in elements:
            raw_urn = _organization_urn_from_acl(item)
            if not raw_urn:
                continue
            urn = _organization_urn(raw_urn)
            if urn in seen:
                continue
            seen.add(urn)
            org_id = _organization_id(urn)
            name = urn
            vanity_name = ""
            page_url = ""
            try:
                org_payload = _request_json(
                    f"/organizations/{org_id}",
                    access_token=access_token,
                    params={"projection": "(id,localizedName,name,vanityName,localizedWebsite,$URN)"},
                )
                vanity_name = org_payload.get("vanityName") or ""
                name = _localized_name(org_payload) or vanity_name or urn
                page_url = _page_url_from_org(org_payload)
            except MarketingServiceError:
                pass
            organizations.append(
                {
                    "id": org_id,
                    "urn": urn,
                    "name": name,
                    "vanity_name": vanity_name,
                    "page_url": page_url,
                }
            )
        if len(elements) < LINKEDIN_PAGE_SIZE:
            break
        start += LINKEDIN_PAGE_SIZE
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
        if share_urn.startswith("urn:li:ugcPost:"):
            params["ugcPosts"] = f"List({share_urn})"
        else:
            params["shares"] = f"List({share_urn})"
    payload = _request_json("/organizationalEntityShareStatistics", access_token=access_token, params=params)
    return payload.get("elements") or []


def _total_share_stats(item: dict[str, Any]) -> dict:
    return item.get("totalShareStatistics") or {}


def fetch_linkedin_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing LinkedIn content id")
    return []


def fetch_linkedin_post_metrics(
    *,
    access_token: str,
    account_id: str,
    content_id: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    if not access_token or not account_id or not content_id:
        raise MarketingServiceError("Missing LinkedIn post analytics credentials")
    rows = []
    for item in _share_statistics(
        access_token=access_token,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        share_urn=content_id,
    ):
        rows.append(_share_metric_row(item, fallback_date=end_date))
    return rows


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
            metric_date = _date_from_millis((item.get("timeRange") or {}).get("start"), end_date)
            gains = item.get("followerGains") or {}
            row = _row_for(rows, metric_date)
            row["followers_change"] = _as_int(gains.get("organicFollowerGain")) + _as_int(gains.get("paidFollowerGain"))
    except MarketingServiceError:
        pass

    try:
        network_payload = _request_json(
            f"https://api.linkedin.com/v2/networkSizes/{_organization_urn(account_id)}",
            access_token=access_token,
            params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"},
        )
        _row_for(rows, end_date)["followers_total"] = _as_int(network_payload.get("firstDegreeSize"))
    except MarketingServiceError:
        pass

    try:
        page_payload = _request_json(
            "/organizationPageStatistics",
            access_token=access_token,
            params={
                "q": "organization",
                "organization": _organization_urn(account_id),
                "timeIntervals": (
                    f"(timeRange:(start:{_date_to_millis(start_date)},end:{_date_to_millis(end_date, end_of_day=True)}),"
                    "timeGranularityType:DAY)"
                ),
            },
        )
        for item in page_payload.get("elements") or []:
            metric_date = _date_from_millis((item.get("timeRange") or {}).get("start"), end_date)
            total = item.get("totalPageStatistics") or {}
            row = _row_for(rows, metric_date)
            row["views"] += _page_views_total(total)
            row["clicks"] += _nested_int_total(total.get("clicks") or {}, key_hints=("click",))
    except MarketingServiceError:
        pass

    for item in _share_statistics(access_token=access_token, account_id=account_id, start_date=start_date, end_date=end_date):
        metric_date = _date_from_millis((item.get("timeRange") or {}).get("start"), end_date)
        stats = _total_share_stats(item)
        row = _row_for(rows, metric_date)
        row["impressions"] += _as_int(stats.get("impressionCount"))
        row["reach"] += _as_int(stats.get("uniqueImpressionsCount"))
        row["clicks"] += _as_int(stats.get("clickCount"))
        row["engagement_total"] += (
            _as_int(stats.get("likeCount"))
            + _as_int(stats.get("commentCount"))
            + _as_int(stats.get("shareCount"))
            + _as_int(stats.get("clickCount"))
        )

    if not rows:
        rows[end_date] = {"date": end_date}
    return [rows[key] for key in sorted(rows)]


def fetch_linkedin_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn account id")
    try:
        payload = _request_json(
            "/organizationalEntityFollowerStatistics",
            access_token=access_token,
            params={"q": "organizationalEntity", "organizationalEntity": _organization_urn(account_id)},
        )
    except MarketingServiceError:
        return []

    country_json: dict[str, int] = {}
    city_json: dict[str, int] = {}
    demographic_json: dict[str, int] = {}
    for item in payload.get("elements") or []:
        for row in item.get("followerCountsByCountry") or []:
            label = row.get("country") or row.get("geo") or row.get("localizedName") or "Unknown"
            country_json[str(label)] = country_json.get(str(label), 0) + _nested_int_total(row, key_hints=("count",))
        for row in item.get("followerCountsByRegion") or []:
            label = row.get("region") or row.get("geo") or row.get("localizedName") or "Unknown"
            city_json[str(label)] = city_json.get(str(label), 0) + _nested_int_total(row, key_hints=("count",))
        for key in ("followerCountsByFunction", "followerCountsByIndustry", "followerCountsBySeniority", "followerCountsByStaffCountRange"):
            for row in item.get(key) or []:
                label = row.get("function") or row.get("industry") or row.get("seniority") or row.get("staffCountRange") or "Unknown"
                demographic_json[str(label)] = demographic_json.get(str(label), 0) + _nested_int_total(row, key_hints=("count",))

    if not (country_json or city_json or demographic_json):
        return []
    return [
        {
            "date": end_date,
            "country_json": country_json,
            "city_json": city_json,
            "gender_age_json": demographic_json,
        }
    ]
