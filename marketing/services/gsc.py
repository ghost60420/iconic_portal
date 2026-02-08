from datetime import date
from typing import List, Dict

from .errors import MarketingServiceError


def fetch_gsc_query_daily(*, access_token: str, site_url: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not site_url:
        raise MarketingServiceError("Missing GSC credentials")
    # TODO: Implement Google Search Console API client in background job.
    # Return a list of dicts like:
    # {"date": date, "query": str, "page": str, "country": str, "device": str, "clicks": int, "impressions": int, "ctr": float, "position": float}
    return []


def fetch_gsc_page_daily(*, access_token: str, site_url: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not site_url:
        raise MarketingServiceError("Missing GSC credentials")
    return []
