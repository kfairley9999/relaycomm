from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from relaycomm.adapters.passthrough import (
    TelnyxPassthroughAdapter,
    VonagePassthroughAdapter,
)
from relaycomm.adapters.sip_stub import SipStubAdapter
from relaycomm.adapters.smtp_gateway import SmtpGatewayAdapter, resolve_gateway_address


def test_resolve_gateway_address():
    assert resolve_gateway_address("+15550109999", "vtext.com") == "5550109999@vtext.com"
    assert (
        resolve_gateway_address("5550109999@txt.att.net", "vtext.com")
        == "5550109999@txt.att.net"
    )


def test_smtp_gateway_adapter_with_local_mock_server():
    received_lines: list[str] = []
    ready = threading.Event()
    port_holder: list[int] = []

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        writer.write(b"220 mock.smtp ESMTP\r\n")
        await writer.drain()
        in_data = False
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            received_lines.append(text)
            if in_data:
                if text == ".":
                    in_data = False
                    writer.write(b"250 OK\r\n")
                    await writer.drain()
                continue
            upper = text.upper()
            if upper.startswith(("EHLO", "HELO")):
                writer.write(b"250-mock.smtp\r\n250 OK\r\n")
            elif upper.startswith("MAIL FROM:") or upper.startswith("RCPT TO:"):
                writer.write(b"250 OK\r\n")
            elif upper == "DATA":
                in_data = True
                writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif upper == "QUIT":
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
        writer.close()

    def run_server():
        async def runner():
            server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
            port_holder.append(server.sockets[0].getsockname()[1])
            ready.set()
            async with server:
                await asyncio.sleep(2.0)

        asyncio.run(runner())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)

    adapter = SmtpGatewayAdapter(
        host="127.0.0.1",
        port=port_holder[0],
        from_addr="relaycomm@example.local",
        default_carrier_domain="vtext.com",
    )

    res = asyncio.run(
        adapter.send_sms(
            message_sid="SM0001",
            to_number="+15550109999",
            from_number="+15550100001",
            body="SMTP test message",
        )
    )
    assert res.status == "sent"
    assert res.metadata["gateway_recipient"] == "5550109999@vtext.com"
    assert any("rcpt to:<5550109999@vtext.com>" in line.lower() for line in received_lines)
    assert any("SMTP test message" in line for line in received_lines)


def test_sip_stub_generates_ari_and_esl_payloads():
    stub = SipStubAdapter(
        engine="asterisk_ari",
        endpoint="http://localhost:8088/ari",
        trunk="voipms-trunk",
    )
    call_res = asyncio.run(
        stub.place_call(
            call_sid="CA1234",
            account_sid="AC1234",
            auth_token="tok",
            to_number="+15550109999",
            from_number="+15550100001",
            url="http://localhost:8080/voice",
            twiml=None,
            method="POST",
        )
    )
    assert call_res.status == "queued"
    assert call_res.metadata["dry_run"] is True
    assert call_res.metadata["ari_payload"]["endpoint"] == "PJSIP/+15550109999@voipms-trunk"
    assert "sofia/gateway/voipms-trunk/+15550109999" in call_res.metadata["esl_command"]


def test_passthrough_requires_keys_and_supports_mock_http():
    telnyx_unconfigured = TelnyxPassthroughAdapter(api_key="")
    res_missing = asyncio.run(
        telnyx_unconfigured.send_sms(
            message_sid="SM1", to_number="+15550109999", from_number="+15550100001", body="hi"
        )
    )
    assert res_missing.status == "failed"

    vonage_unconfigured = VonagePassthroughAdapter(api_key="", api_secret="")
    res_v_missing = asyncio.run(
        vonage_unconfigured.send_sms(
            message_sid="SM2", to_number="+15550109999", from_number="+15550100001", body="hi"
        )
    )
    assert res_v_missing.status == "failed"

    def telnyx_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test_telnyx_key"
        return httpx.Response(200, json={"data": {"id": "telnyx_msg_123"}})

    telnyx = TelnyxPassthroughAdapter(
        api_key="test_telnyx_key",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(telnyx_handler)
        ),
    )
    res_ok = asyncio.run(
        telnyx.send_sms(
            message_sid="SM3", to_number="+15550109999", from_number="+15550100001", body="hi"
        )
    )
    assert res_ok.status == "queued"
    assert res_ok.provider_message_id == "telnyx_msg_123"
