"""Signed webhook and TwiML HTTP dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from relaycomm.db import Database
from relaycomm.security import compute_twilio_signature


@dataclass
class WebhookResponse:
    status_code: int | None
    text: str
    signature: str
    error: str | None = None


class WebhookDispatcher:
    """Sends Twilio-compatible form-encoded requests signed with X-Twilio-Signature."""

    def __init__(
        self,
        db: Database,
        *,
        timeout: float = 5.0,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.db = db
        self.timeout = timeout
        self._client_factory = client_factory

    def _make_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

    async def dispatch(
        self,
        *,
        account_sid: str,
        auth_token: str,
        resource_sid: str,
        event: str,
        url: str,
        params: dict[str, Any],
        method: str = "POST",
        base_url: str | None = None,
    ) -> WebhookResponse:
        target_url = urljoin(base_url + "/", url) if base_url and url.startswith("/") else url
        str_params = {k: ("" if v is None else str(v)) for k, v in params.items()}
        upper_method = (method or "POST").upper()

        # Per Twilio specification: for POST, sign full URL + sorted form params;
        # for GET, query parameters belong in the URL.
        sign_params = str_params if upper_method == "POST" else None
        signature = compute_twilio_signature(auth_token, target_url, sign_params)

        headers = {
            "X-Twilio-Signature": signature,
            "X-Relaycomm-Signature": signature,
            "User-Agent": "RelaycommProxy/0.1.0",
        }

        status_code: int | None = None
        text = ""
        error: str | None = None

        try:
            async with self._make_client() as client:
                if upper_method == "GET":
                    resp = await client.get(target_url, params=str_params, headers=headers)
                else:
                    resp = await client.post(target_url, data=str_params, headers=headers)
                status_code = resp.status_code
                text = resp.text
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        self.db.log_webhook_delivery(
            account_sid=account_sid,
            resource_sid=resource_sid,
            event=event,
            target_url=target_url,
            method=upper_method,
            request_body=str_params,
            signature=signature,
            status_code=status_code,
            response_snippet=text[:1000] if text else None,
            error=error,
        )

        return WebhookResponse(
            status_code=status_code,
            text=text,
            signature=signature,
            error=error,
        )
