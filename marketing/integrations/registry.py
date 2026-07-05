from __future__ import annotations

from .base import MarketingIntegrationAdapter, WaitingAdapter


_ADAPTERS: dict[str, MarketingIntegrationAdapter] = {
    key: WaitingAdapter(key, label)
    for key, label in (
        ("google_trends", "Google Trends"),
        ("google_search_console", "Google Search Console"),
        ("google_analytics", "Google Analytics"),
        ("google_business", "Google Business Profile"),
        ("linkedin", "LinkedIn Analytics"),
        ("meta", "Meta"),
        ("facebook", "Facebook"),
        ("instagram", "Instagram"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("openai", "OpenAI"),
        ("claude", "Claude"),
        ("gemini", "Gemini"),
    )
}


def register_adapter(adapter: MarketingIntegrationAdapter) -> None:
    """Replace a placeholder when an approved provider implementation is added."""
    _ADAPTERS[adapter.key] = adapter


def get_adapter(key: str) -> MarketingIntegrationAdapter:
    return _ADAPTERS[key]


def integration_statuses() -> list:
    """Return local adapter readiness only; providers are never called here."""
    return [adapter.status() for adapter in _ADAPTERS.values()]
