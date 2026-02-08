from datetime import date
from typing import List, Dict

from .errors import MarketingServiceError


def fetch_ga4_daily(*, access_token: str, property_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not property_id:
        raise MarketingServiceError("Missing GA4 credentials")
    return []
