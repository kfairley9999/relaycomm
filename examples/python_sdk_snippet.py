"""Python examples showing how to call Relaycomm with standard HTTP or the official Twilio SDK.

Usage:
    python examples/python_sdk_snippet.py
"""

from __future__ import annotations

import os

import httpx

BASE_URL = os.getenv("RELAYCOMM_BASE_URL", "http://localhost:8080")
ACCOUNT_SID = os.getenv("RELAYCOMM_ACCOUNT_SID", "AC00000000000000000000000000000001")
AUTH_TOKEN = os.getenv("RELAYCOMM_AUTH_TOKEN", "dev_auth_token_change_me")


def send_sms_with_httpx() -> dict:
    """Drop-in REST call using HTTP Basic Auth and form-encoded fields."""
    resp = httpx.post(
        f"{BASE_URL}/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json",
        auth=(ACCOUNT_SID, AUTH_TOKEN),
        data={
            "To": "+15550109999",
            "From": "+15550100001",
            "Body": "Hello from Relaycomm Python snippet!",
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


def place_twiml_call_with_httpx() -> dict:
    """Place a simulated voice call with inline TwiML."""
    twiml = (
        "<Response>"
        '<Say voice="alice">Thanks for calling Relaycomm.</Say>'
        '<Gather numDigits="1"><Say>Press 1 for sales.</Say></Gather>'
        "<Hangup/>"
        "</Response>"
    )
    resp = httpx.post(
        f"{BASE_URL}/2010-04-01/Accounts/{ACCOUNT_SID}/Calls.json",
        auth=(ACCOUNT_SID, AUTH_TOKEN),
        data={
            "To": "+15550109999",
            "From": "+15550100001",
            "Twiml": twiml,
            "SimulatedDigits": "1",
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


def twilio_official_sdk_snippet() -> None:
    """Example showing how to point the official `twilio` Python SDK at Relaycomm.

    Requires: pip install twilio
    """
    try:
        from twilio.http.http_client import TwilioHttpClient
        from twilio.rest import Client
    except ImportError:
        print("Optional `twilio` package not installed; skipping official SDK demo.")
        return

    class RelaycommHttpClient(TwilioHttpClient):
        def request(self, method, url, *args, **kwargs):
            # Rewrite api.twilio.com host to self-hosted Relaycomm base URL
            rewritten = url.replace("https://api.twilio.com", BASE_URL)
            return super().request(method, rewritten, *args, **kwargs)

    client = Client(ACCOUNT_SID, AUTH_TOKEN, http_client=RelaycommHttpClient())
    msg = client.messages.create(
        to="+15550109999",
        from_="+15550100001",
        body="Sent via official Twilio SDK pointed at Relaycomm!",
    )
    print("Official SDK Message SID:", msg.sid, "Status:", msg.status)


if __name__ == "__main__":
    print("SMS:", send_sms_with_httpx())
    print("Call:", place_twiml_call_with_httpx())
    twilio_official_sdk_snippet()
