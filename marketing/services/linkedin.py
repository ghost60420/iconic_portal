from datetime import date
from typing import List, Dict

from .errors import MarketingServiceError


def fetch_linkedin_content(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn credentials")
    return []


def fetch_linkedin_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing LinkedIn content id")
    return []


def fetch_linkedin_account_metrics(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn account id")
    return []


def fetch_linkedin_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing LinkedIn account id")
    return []
