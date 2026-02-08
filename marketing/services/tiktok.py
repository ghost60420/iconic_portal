from datetime import date
from typing import List, Dict

from .errors import MarketingServiceError


def fetch_tiktok_content(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok credentials")
    return []


def fetch_tiktok_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing TikTok content id")
    return []


def fetch_tiktok_account_metrics(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok account id")
    return []


def fetch_tiktok_audience(*, access_token: str, account_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not account_id:
        raise MarketingServiceError("Missing TikTok account id")
    return []
