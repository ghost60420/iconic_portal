from datetime import date, datetime
from decimal import Decimal
from typing import Any

from marketing.services.errors import MarketingServiceError
from marketing.services.google_oauth import google_api_request_json


GA4_RUN_REPORT_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
COMMON_METRICS = [
    "totalUsers",
    "sessions",
    "screenPageViews",
    "bounceRate",
    "averageSessionDuration",
]


def _property_id(raw: str) -> str:
    return (raw or "").replace("properties/", "").strip()


def _parse_ga4_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _metric_map(row: dict[str, Any], metric_names: list[str]) -> dict[str, str]:
    values = row.get("metricValues", [])
    return {
        metric_names[index]: values[index].get("value", "0")
        for index in range(min(len(metric_names), len(values)))
    }


def _dimension_map(row: dict[str, Any], dimension_names: list[str]) -> dict[str, str]:
    values = row.get("dimensionValues", [])
    return {
        dimension_names[index]: values[index].get("value", "")
        for index in range(min(len(dimension_names), len(values)))
    }


def _int_metric(metrics: dict[str, str], key: str) -> int:
    try:
        return int(float(metrics.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _decimal_metric(metrics: dict[str, str], key: str) -> Decimal:
    try:
        return Decimal(str(metrics.get(key) or "0"))
    except Exception:
        return Decimal("0")


def _run_report(*, access_token: str, property_id: str, start_date: date, end_date: date, dimensions: list[str], metrics: list[str], limit: int = 10000) -> list[dict[str, Any]]:
    payload = {
        "dateRanges": [
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            }
        ],
        "dimensions": [{"name": name} for name in dimensions],
        "metrics": [{"name": name} for name in metrics],
        "limit": limit,
    }
    response = google_api_request_json(
        GA4_RUN_REPORT_URL.format(property_id=_property_id(property_id)),
        method="POST",
        payload=payload,
        access_token=access_token,
    )
    return response.get("rows", [])


def _traffic_payload(*, row: dict[str, Any], dimension_names: list[str], metric_names: list[str], row_type: str) -> dict:
    dimensions = _dimension_map(row, dimension_names)
    metrics = _metric_map(row, metric_names)
    sessions = _int_metric(metrics, "sessions")
    bounce_rate = _decimal_metric(metrics, "bounceRate")
    engaged_sessions = max(int(sessions * max(Decimal("0"), (Decimal("1") - bounce_rate))), 0)
    avg_session_duration = _int_metric(metrics, "averageSessionDuration")
    payload = {
        "date": _parse_ga4_date(dimensions.get("date", "")),
        "row_type": row_type,
        "visitors": _int_metric(metrics, "totalUsers"),
        "sessions": sessions,
        "engaged_sessions": engaged_sessions,
        "page_views": _int_metric(metrics, "screenPageViews"),
        "bounce_rate": bounce_rate,
        "avg_session_duration_seconds": avg_session_duration,
        "avg_engagement_seconds": avg_session_duration,
    }
    if row_type == "overall":
        payload["channel"] = "All Traffic"
    elif row_type == "source":
        payload.update(
            {
                "channel": dimensions.get("sessionDefaultChannelGroup", ""),
                "source": dimensions.get("sessionSource", ""),
                "medium": dimensions.get("sessionMedium", ""),
                "campaign": dimensions.get("sessionCampaignName", ""),
            }
        )
    elif row_type == "country":
        payload.update({"channel": "Country", "country": dimensions.get("country", "")})
    elif row_type == "device":
        payload.update({"channel": "Device", "device": dimensions.get("deviceCategory", "")})
    return payload


def _page_payload(*, row: dict[str, Any], dimension_names: list[str], metric_names: list[str]) -> dict:
    dimensions = _dimension_map(row, dimension_names)
    metrics = _metric_map(row, metric_names)
    avg_session_duration = _int_metric(metrics, "averageSessionDuration")
    return {
        "date": _parse_ga4_date(dimensions.get("date", "")),
        "page_path": dimensions.get("pagePathPlusQueryString", "") or dimensions.get("pageLocation", ""),
        "page_title": dimensions.get("pageTitle", ""),
        "visitors": _int_metric(metrics, "totalUsers"),
        "sessions": _int_metric(metrics, "sessions"),
        "page_views": _int_metric(metrics, "screenPageViews"),
        "avg_engagement_seconds": avg_session_duration,
    }


def fetch_ga4_daily(*, access_token: str, property_id: str, start_date: date, end_date: date) -> dict[str, list[dict]]:
    if not access_token or not property_id:
        raise MarketingServiceError("Missing GA4 credentials")

    traffic_rows = []
    page_rows = []

    report_specs = [
        ("overall", ["date"]),
        ("source", ["date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium", "sessionCampaignName"]),
    ]
    for row_type, dimensions in report_specs:
        for row in _run_report(
            access_token=access_token,
            property_id=property_id,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
            metrics=COMMON_METRICS,
        ):
            traffic_rows.append(
                _traffic_payload(
                    row=row,
                    dimension_names=dimensions,
                    metric_names=COMMON_METRICS,
                    row_type=row_type,
                )
            )

    page_dimensions = ["date", "pagePathPlusQueryString", "pageTitle"]
    for row in _run_report(
        access_token=access_token,
        property_id=property_id,
        start_date=start_date,
        end_date=end_date,
        dimensions=page_dimensions,
        metrics=COMMON_METRICS,
    ):
        page_rows.append(_page_payload(row=row, dimension_names=page_dimensions, metric_names=COMMON_METRICS))

    return {"traffic_rows": traffic_rows, "page_rows": page_rows}
