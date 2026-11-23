"""SMTP email-to-SMS gateway adapter.

Maps US/NANP or explicit email-formatted recipients to carrier SMS email gateways
(e.g. 5551234567@vtext.com, txt.att.net, tmomail.net) and sends via SMTP.
"""

from __future__ import annotations

import asyncio
import re
import smtplib
from email.message import EmailMessage
from typing import Any

from relaycomm.adapters.base import AdapterCallResult, AdapterSmsResult, CarrierAdapter

CARRIER_GATEWAYS = {
    "verizon": "vtext.com",
    "att": "txt.att.net",
    "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "uscellular": "email.uscc.net",
    "googlefi": "msg.fi.google.com",
}


def resolve_gateway_address(to_number: str, default_domain: str) -> str:
    """Resolve a phone number or carrier-qualified string to an SMTP gateway email."""
    if "@" in to_number:
        return to_number.strip()
    digits = re.sub(r"\D", "", to_number)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"{digits or to_number}@{default_domain}"


class SmtpGatewayAdapter(CarrierAdapter):
    name = "smtp_gateway"
    description = (
        "Free SMTP email-to-SMS gateway adapter (e.g. Mailpit/Postfix/ISP SMTP -> "
        "vtext.com / txt.att.net / tmomail.net). Voice calls are unsupported."
    )
    is_live_pstn = False

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_addr: str,
        default_carrier_domain: str = "vtext.com",
        username: str = "",
        password: str = "",
        use_tls: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.from_addr = from_addr
        self.default_carrier_domain = default_carrier_domain
        self.username = username
        self.password = password
        self.use_tls = use_tls

    def _send_blocking(self, recipient: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(self.host, self.port, timeout=5) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(msg)

    async def send_sms(
        self,
        *,
        message_sid: str,
        to_number: str,
        from_number: str,
        body: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterSmsResult:
        opts = options or {}
        carrier = str(opts.get("carrier", "")).lower()
        domain = CARRIER_GATEWAYS.get(
            carrier, str(opts.get("carrier_domain") or self.default_carrier_domain)
        )
        recipient = resolve_gateway_address(to_number, domain)
        subject = f"SMS from {from_number} [{message_sid}]"

        try:
            await asyncio.to_thread(self._send_blocking, recipient, subject, body)
        except Exception as exc:
            return AdapterSmsResult(
                status="failed",
                error_code=30008,
                error_message=f"SMTP gateway error ({self.host}:{self.port}): {exc}",
                metadata={"gateway_recipient": recipient, "smtp_host": self.host},
            )

        return AdapterSmsResult(
            status="sent",
            provider_message_id=f"smtp_{message_sid}",
            metadata={
                "gateway_recipient": recipient,
                "smtp_host": self.host,
                "smtp_port": self.port,
            },
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
        return AdapterCallResult(
            status="failed",
            duration="0",
            error_message="smtp_gateway adapter only supports SMS, not Voice calls.",
            metadata={"supported": False},
        )
