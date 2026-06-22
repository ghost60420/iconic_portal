from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from marketing.services.errors import MarketingServiceError
from marketing.services.google_oauth import google_api_request_json


GSC_SEARCH_ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"


def _int_value(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _decimal_value(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _query_search_analytics(*, access_token: str, site_url: str, start_date: date, end_date: date, dimensions: list[str], row_limit: int = 25000) -> list[dict[str, Any]]:
    payload = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "dataState": "final",
    }
    response = google_api_request_json(
        GSC_SEARCH_ANALYTICS_URL.format(site_url=quote(site_url, safe="")),
        method="POST",
        payload=payload,
        access_token=access_token,
    )
    return response.get("rows", [])


def _row_payload(row: dict[str, Any], dimensions: list[str]) -> dict:
    values = row.get("keys", [])
    dimension_map = {
        dimensions[index]: values[index]
        for index in range(min(len(dimensions), len(values)))
    }
    return {
        "date": date.fromisoformat(dimension_map.get("date")),
        "query": dimension_map.get("query", ""),
        "page": dimension_map.get("page", ""),
        "country": dimension_map.get("country", ""),
        "device": dimension_map.get("device", ""),
        "clicks": _int_value(row.get("clicks")),
        "impressions": _int_value(row.get("impressions")),
        "ctr": _decimal_value(row.get("ctr")),
        "position": _decimal_value(row.get("position")),
    }


def fetch_gsc_query_daily(*, access_token: str, site_url: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not site_url:
        raise MarketingServiceError("Missing GSC credentials")
    dimensions = ["date", "query", "page", "country", "device"]
    return [
        _row_payload(row, dimensions)
        for row in _query_search_analytics(
            access_token=access_token,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
        )
    ]


def fetch_gsc_page_daily(*, access_token: str, site_url: str, start_date: date, end_date: date) -> list[dict]:
    if not access_token or not site_url:
        raise MarketingServiceError("Missing GSC credentials")
    dimensions = ["date", "page"]
    return [
        _row_payload(row, dimensions)
        for row in _query_search_analytics(
            access_token=access_token,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
        )
    ]
