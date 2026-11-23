"""Abstract base classes and result dataclasses for carrier adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdapterSmsResult:
    status: str  # sent | delivered | queued | failed
    provider_message_id: str | None = None
    error_code: int | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterCallResult:
    status: str  # queued | ringing | in-progress | completed | failed
    duration: str = "0"
    twiml_log: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CarrierAdapter(ABC):
    name: str = "base"
    description: str = ""
    is_live_pstn: bool = False

    @abstractmethod
    async def send_sms(
        self,
        *,
        message_sid: str,
        to_number: str,
        from_number: str,
        body: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterSmsResult:
        """Send an outbound SMS via this adapter."""

    @abstractmethod
    async def place_call(
        self,
        *,
        call_sid: str,
        account_sid: str,
        auth_token: str,
        to_number: str,
        from_number: str,
        url: str | None,
        twiml: str | None,
        method: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterCallResult:
        """Initiate an outbound voice call via this adapter."""

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "is_live_pstn": self.is_live_pstn,
        }
