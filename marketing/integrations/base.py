from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IntegrationStatus:
    key: str
    label: str
    status: str
    message: str


class MarketingIntegrationAdapter(Protocol):
    key: str
    label: str

    def is_available(self) -> bool: ...

    def status(self) -> IntegrationStatus: ...


class WaitingAdapter:
    """Safe placeholder. It never performs network I/O."""

    def __init__(self, key: str, label: str):
        self.key = key
        self.label = label

    def is_available(self) -> bool:
        return False

    def status(self) -> IntegrationStatus:
        return IntegrationStatus(
            key=self.key,
            label=self.label,
            status="waiting",
            message="Waiting for API",
        )
