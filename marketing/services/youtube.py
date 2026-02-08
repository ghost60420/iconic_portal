from datetime import date
from typing import List, Dict

from .errors import MarketingServiceError


def fetch_youtube_content(*, access_token: str, channel_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not channel_id:
        raise MarketingServiceError("Missing YouTube credentials")
    return []


def fetch_youtube_metrics(*, access_token: str, content_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not content_id:
        raise MarketingServiceError("Missing YouTube content id")
    return []


def fetch_youtube_account_metrics(*, access_token: str, channel_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not channel_id:
        raise MarketingServiceError("Missing YouTube channel id")
    return []


def fetch_youtube_audience(*, access_token: str, channel_id: str, start_date: date, end_date: date) -> List[Dict]:
    if not access_token or not channel_id:
        raise MarketingServiceError("Missing YouTube channel id")
    return []
