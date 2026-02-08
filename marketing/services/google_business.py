from datetime import date
from typing import List, Dict

from .errors import MarketingServiceError


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
    return []


def fetch_google_business_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing Google Business account id")
    return []
