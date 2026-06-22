from datetime import date
from typing import List, Dict
from urllib.parse import urlencode

from marketing.services.google_oauth import google_api_request_json
from .errors import MarketingServiceError


GBP_PERFORMANCE_URL = "https://businessprofileperformance.googleapis.com/v1"
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


def _google_date(value: dict, fallback: date) -> date:
    if not isinstance(value, dict):
        return fallback
    try:
        return date(int(value.get("year")), int(value.get("month")), int(value.get("day")))
    except (TypeError, ValueError):
        return fallback


def fetch_google_business_content(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Google Business credentials")
    return []


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
