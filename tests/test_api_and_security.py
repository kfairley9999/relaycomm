from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from relaycomm.app import create_app
from relaycomm.config import Settings
from relaycomm.security import compute_twilio_signature, verify_twilio_signature

ACCOUNT_SID = "AC00000000000000000000000000000001"
AUTH_TOKEN = "test_secret_token_123"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings(
        host="127.0.0.1",
        port=8080,
        db_path=str(tmp_path / "test.db"),
        account_sid=ACCOUNT_SID,
        auth_token=AUTH_TOKEN,
        adapter="simulator",
        base_url="http://testserver",
        smtp_host="localhost",
        smtp_port=1025,
        smtp_user="",
        smtp_password="",
        smtp_from="relaycomm@example.local",
        smtp_use_tls=False,
        smtp_default_carrier_domain="vtext.com",
        sip_engine="asterisk_ari",
        sip_endpoint="http://localhost:8088/ari",
        sip_trunk="trunk-primary",
        telnyx_api_key="",
        vonage_api_key="",
        vonage_api_secret="",
        webhook_timeout_seconds=3.0,
    )
    return Settings(**{**base.__dict__, **overrides})


def test_twilio_signature_matches_spec():
    # Known Twilio docs test vector
    token = "12345"
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675310",
        "Digits": "1234",
        "From": "+14158675310",
        "To": "+18005551212",
    }
    sig = compute_twilio_signature(token, url, params)
    assert sig == "GvWf1cFY/Q7PnoempGyD5oXAezc="
    assert verify_twilio_signature(token, url, params, sig) is True
    assert verify_twilio_signature(token, url, params, "wrong") is False


def test_health_and_console_ui(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "relaycomm"
        assert r.json()["adapter"] == "simulator"

        html = client.get("/")
        assert html.status_code == 200
        assert "Relaycomm Console" in html.text


def test_authentication_and_account_endpoint(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        # Missing auth -> 401
        r_no_auth = client.get(f"/2010-04-01/Accounts/{ACCOUNT_SID}.json")
        assert r_no_auth.status_code == 401
        assert r_no_auth.json()["code"] == 20003

        # Bad token -> 401
        r_bad = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}.json",
            auth=(ACCOUNT_SID, "wrong_token"),
        )
        assert r_bad.status_code == 401

        # Path AccountSid mismatch -> 403
        r_mismatch = client.get(
            "/2010-04-01/Accounts/AC99999999999999999999999999999999.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        )
        assert r_mismatch.status_code == 403

        # Valid -> 200
        r_ok = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["sid"] == ACCOUNT_SID


def test_messages_crud_and_signed_status_callback(tmp_path: Path):
    received_callbacks: list[dict] = []
    receiver = FastAPI()

    @receiver.post("/status")
    async def status_cb(request: Request):
        raw_body = (await request.body()).decode("utf-8")
        parsed = {k: v[0] for k, v in parse_qs(raw_body, keep_blank_values=True).items()}
        sig = request.headers.get("X-Twilio-Signature", "")
        received_callbacks.append(
            {
                "params": parsed,
                "sig": sig,
                "valid": verify_twilio_signature(
                    AUTH_TOKEN, "http://webhook.local/status", parsed, sig
                ),
            }
        )
        return PlainTextResponse("ok")

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=receiver))

    app = create_app(make_settings(tmp_path), webhook_client_factory=client_factory)

    with TestClient(app) as client:
        # Create via form-urlencoded (Twilio standard)
        r = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            data={
                "From": "+15550100001",
                "To": "+15550109999",
                "Body": "Hello test 1",
                "StatusCallback": "http://webhook.local/status",
            },
        )
        assert r.status_code == 201
        msg1 = r.json()
        assert msg1["sid"].startswith("SM")
        assert len(msg1["sid"]) == 34
        assert msg1["status"] == "delivered"
        assert msg1["num_segments"] == "1"

        # Verify signed callback
        assert len(received_callbacks) == 1
        assert received_callbacks[0]["valid"] is True
        assert received_callbacks[0]["params"]["MessageSid"] == msg1["sid"]
        assert received_callbacks[0]["params"]["MessageStatus"] == "delivered"

        # Create long message via JSON -> 2 segments
        r2 = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            json={
                "From": "+15550100002",
                "To": "+15550108888",
                "Body": "A" * 180,
            },
        )
        assert r2.status_code == 201
        msg2 = r2.json()
        assert msg2["num_segments"] == "2"

        # List with To filter
        r_list = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            params={"To": "+15550109999"},
        )
        assert r_list.status_code == 200
        assert len(r_list.json()["messages"]) == 1
        assert r_list.json()["messages"][0]["sid"] == msg1["sid"]

        # Get single
        r_get = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/{msg1['sid']}.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        )
        assert r_get.status_code == 200
        assert r_get.json()["body"] == "Hello test 1"

        # Delete
        r_del = client.delete(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/{msg1['sid']}.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        )
        assert r_del.status_code == 204
        r_missing = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/{msg1['sid']}.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        )
        assert r_missing.status_code == 404
