from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from relaycomm.app import create_app
from relaycomm.config import Settings
from relaycomm.security import verify_twilio_signature

ACCOUNT_SID = "AC00000000000000000000000000000001"
AUTH_TOKEN = "test_secret_token_123"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8080,
        db_path=str(tmp_path / "calls.db"),
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


def test_inline_twiml_verbs_and_recording(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    twiml = """
    <Response>
      <Say voice="alice">Welcome to Relaycomm.</Say>
      <Play>https://example.local/chime.mp3</Play>
      <Dial callerId="+15550100001"><Number>+15550107777</Number></Dial>
      <Record maxLength="10" playBeep="true" />
      <Hangup />
    </Response>
    """
    with TestClient(app) as client:
        r = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            data={
                "From": "+15550100001",
                "To": "+15550109999",
                "Twiml": twiml,
            },
        )
        assert r.status_code == 201
        call = r.json()
        assert call["sid"].startswith("CA")
        assert call["status"] == "completed"
        assert int(call["duration"]) >= 10

        trace = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/{call['sid']}/Trace.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        ).json()
        verbs = [s["verb"] for s in trace["twiml_log"]]
        assert verbs == ["Say", "Play", "Dial", "Record", "Hangup"]

        recs = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/{call['sid']}/Recordings.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        ).json()["recordings"]
        assert len(recs) == 1
        assert recs[0]["sid"].startswith("RE")

        single_rec = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/{call['sid']}/Recordings/{recs[0]['sid']}.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        )
        assert single_rec.status_code == 200
        assert single_rec.json()["call_sid"] == call["sid"]

        # Update call status
        upd = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/{call['sid']}.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            data={"Status": "completed"},
        )
        assert upd.status_code == 200


def test_remote_twiml_gather_flow_and_signatures(tmp_path: Path):
    twiml_app = FastAPI()
    captured_signatures: list[bool] = []

    @twiml_app.post("/voice")
    async def voice_entry(request: Request):
        raw = (await request.body()).decode("utf-8")
        params = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        sig = request.headers.get("X-Twilio-Signature", "")
        captured_signatures.append(
            verify_twilio_signature(AUTH_TOKEN, "http://twiml.local/voice", params, sig)
        )
        xml = """
        <Response>
          <Gather numDigits="1" action="http://twiml.local/gather" method="POST">
            <Say>Press 2 for support.</Say>
          </Gather>
        </Response>
        """
        return Response(content=xml, media_type="application/xml")

    @twiml_app.post("/gather")
    async def gather_action(request: Request):
        raw = (await request.body()).decode("utf-8")
        params = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        sig = request.headers.get("X-Twilio-Signature", "")
        captured_signatures.append(
            verify_twilio_signature(AUTH_TOKEN, "http://twiml.local/gather", params, sig)
        )
        digits = params.get("Digits", "")
        xml = f"<Response><Say>Connecting option {digits}.</Say><Hangup/></Response>"
        return Response(content=xml, media_type="application/xml")

    app = create_app(
        make_settings(tmp_path),
        webhook_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=twiml_app)
        ),
    )

    with TestClient(app) as client:
        r = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            data={
                "From": "+15550100001",
                "To": "+15550109999",
                "Url": "http://twiml.local/voice",
                "SimulatedDigits": "299",
            },
        )
        assert r.status_code == 201
        call = r.json()
        assert call["status"] == "completed"
        assert captured_signatures == [True, True]

        trace = client.get(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/{call['sid']}/Trace.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
        ).json()
        assert trace["twiml_log"][0]["verb"] == "Gather"
        assert trace["twiml_log"][0]["digits_received"] == "2"
        assert trace["twiml_log"][1]["verb"] == "Say"
        assert "Connecting option 2." in trace["twiml_log"][1]["text"]
        assert trace["twiml_log"][2]["verb"] == "Hangup"


def test_xxe_blocked_and_redirect_limit(tmp_path: Path):
    loop_app = FastAPI()

    @loop_app.post("/loop")
    async def loop_twiml():
        return Response(
            content="<Response><Redirect>http://twiml.local/loop</Redirect></Response>",
            media_type="application/xml",
        )

    app = create_app(
        make_settings(tmp_path),
        webhook_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=loop_app)
        ),
    )

    xxe_payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<Response><Say>&xxe;</Say></Response>"
    )

    with TestClient(app) as client:
        r_xxe = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            data={
                "From": "+15550100001",
                "To": "+15550109999",
                "Twiml": xxe_payload,
            },
        )
        assert r_xxe.status_code == 201
        assert r_xxe.json()["status"] == "failed"

        r_loop = client.post(
            f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls.json",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            data={
                "From": "+15550100001",
                "To": "+15550109999",
                "Url": "http://twiml.local/loop",
            },
        )
        assert r_loop.status_code == 201
        assert r_loop.json()["status"] == "failed"


def test_inbound_simulation_and_console_state(tmp_path: Path):
    hook_app = FastAPI()

    @hook_app.post("/inbound-sms")
    async def inbound_sms():
        return Response(content="<Response/>", media_type="application/xml")

    @hook_app.post("/inbound-voice")
    async def inbound_voice():
        return Response(
            content="<Response><Say>Inbound answered</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    app = create_app(
        make_settings(tmp_path),
        webhook_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=hook_app)
        ),
    )

    with TestClient(app) as client:
        r_sms = client.post(
            "/console/api/simulate/inbound-sms",
            json={
                "From": "+15550108888",
                "To": "+15550100001",
                "Body": "Hi Relaycomm",
                "WebhookUrl": "http://hook.local/inbound-sms",
            },
        )
        assert r_sms.status_code == 201
        assert r_sms.json()["message"]["direction"] == "inbound"
        assert r_sms.json()["webhook"]["status_code"] == 200

        r_call = client.post(
            "/console/api/simulate/inbound-call",
            json={
                "From": "+15550108888",
                "To": "+15550100001",
                "WebhookUrl": "http://hook.local/inbound-voice",
            },
        )
        assert r_call.status_code == 201
        assert r_call.json()["status"] == "completed"
        assert r_call.json()["twiml_log"][0]["text"] == "Inbound answered"

        state = client.get("/console/api/state").json()
        assert state["stats"]["messages"] == 1
        assert state["stats"]["calls"] == 1
        assert state["stats"]["webhooks"] == 2
