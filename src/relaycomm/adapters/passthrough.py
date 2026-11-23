"""Optional Telnyx and Vonage carrier passthrough adapters.

These adapters are disabled unless explicitly selected AND valid API credentials
are supplied via environment configuration.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from relaycomm.adapters.base import AdapterCallResult, AdapterSmsResult, CarrierAdapter


class TelnyxPassthroughAdapter(CarrierAdapter):
    name = "telnyx"
    description = "Optional Telnyx v2 API passthrough (requires paid Telnyx API key)."
    is_live_pstn = True

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.telnyx.com/v2",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client_factory = client_factory

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=10.0)

    async def send_sms(
        self,
        *,
        message_sid: str,
        to_number: str,
        from_number: str,
        body: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterSmsResult:
        if not self.api_key:
            return AdapterSmsResult(
                status="failed",
                error_code=20003,
                error_message="RELAYCOMM_TELNYX_API_KEY is not configured.",
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"from": from_number, "to": to_number, "text": body}
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self.base_url}/messages", json=payload, headers=headers
                )
            if resp.status_code >= 400:
                return AdapterSmsResult(
                    status="failed",
                    error_code=resp.status_code,
                    error_message=f"Telnyx error {resp.status_code}: {resp.text[:300]}",
                )
            data = resp.json().get("data", {})
            return AdapterSmsResult(
                status="queued",
                provider_message_id=data.get("id", f"telnyx_{message_sid}"),
                metadata={"provider": "telnyx", "raw": data},
            )
        except Exception as exc:
            return AdapterSmsResult(
                status="failed",
                error_code=30008,
                error_message=f"Telnyx request failed: {exc}",
            )

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
        if not self.api_key:
            return AdapterCallResult(
                status="failed",
                error_message="RELAYCOMM_TELNYX_API_KEY is not configured.",
            )
        opts = options or {}
        connection_id = opts.get("connection_id")
        if not connection_id:
            return AdapterCallResult(
                status="failed",
                error_message="Telnyx Call Control requires options.connection_id.",
            )
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "connection_id": connection_id,
            "to": to_number,
            "from": from_number,
            "webhook_url": url,
        }
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self.base_url}/calls", json=payload, headers=headers
                )
            if resp.status_code >= 400:
                return AdapterCallResult(
                    status="failed",
                    error_message=f"Telnyx error {resp.status_code}: {resp.text[:300]}",
                )
            return AdapterCallResult(
                status="queued",
                metadata={"provider": "telnyx", "raw": resp.json()},
            )
        except Exception as exc:
            return AdapterCallResult(
                status="failed",
                error_message=f"Telnyx call request failed: {exc}",
            )


class VonagePassthroughAdapter(CarrierAdapter):
    name = "vonage"
    description = "Optional Vonage SMS/Voice API passthrough (requires paid Vonage keys)."
    is_live_pstn = True

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        sms_url: str = "https://rest.nexmo.com/sms/json",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.sms_url = sms_url
        self._client_factory = client_factory

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=10.0)

    async def send_sms(
        self,
        *,
        message_sid: str,
        to_number: str,
        from_number: str,
        body: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterSmsResult:
        if not self.api_key or not self.api_secret:
            return AdapterSmsResult(
                status="failed",
                error_code=20003,
                error_message="RELAYCOMM_VONAGE_API_KEY and RELAYCOMM_VONAGE_API_SECRET must be configured.",
            )
        payload = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "from": from_number.lstrip("+"),
            "to": to_number.lstrip("+"),
            "text": body,
        }
        try:
            async with self._client() as client:
                resp = await client.post(self.sms_url, json=payload)
            if resp.status_code >= 400:
                return AdapterSmsResult(
                    status="failed",
                    error_code=resp.status_code,
                    error_message=f"Vonage error {resp.status_code}: {resp.text[:300]}",
                )
            data = resp.json()
            messages = data.get("messages", [{}])
            first = messages[0] if messages else {}
            if first.get("status", "0") != "0":
                return AdapterSmsResult(
                    status="failed",
                    error_code=int(first.get("status", 1)),
                    error_message=first.get("error-text", "Vonage SMS rejected"),
                )
            return AdapterSmsResult(
                status="sent",
                provider_message_id=first.get("message-id", f"vonage_{message_sid}"),
                metadata={"provider": "vonage", "raw": data},
            )
        except Exception as exc:
            return AdapterSmsResult(
                status="failed",
                error_code=30008,
                error_message=f"Vonage request failed: {exc}",
            )

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
        if not self.api_key or not self.api_secret:
            return AdapterCallResult(
                status="failed",
                error_message="RELAYCOMM_VONAGE_API_KEY and RELAYCOMM_VONAGE_API_SECRET must be configured.",
            )
        return AdapterCallResult(
            status="failed",
            error_message=(
                "Vonage Voice API requires a JWT signed with a Vonage Application private key; "
                "use sip_stub or simulator for local voice flows."
            ),
        )
