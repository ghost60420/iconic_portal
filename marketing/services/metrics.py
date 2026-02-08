from __future__ import annotations


def calc_engagement_total(*, likes: int = 0, comments: int = 0, shares: int = 0, saves: int = 0) -> int:
    return max(int(likes or 0), 0) + max(int(comments or 0), 0) + max(int(shares or 0), 0) + max(int(saves or 0), 0)


def calc_engagement_rate(*, impressions: int = 0, reach: int = 0, views: int = 0, engagement_total: int = 0) -> float:
    denom = impressions or reach or views or 0
    if denom <= 0:
        return 0.0
    return float(engagement_total) / float(denom)
