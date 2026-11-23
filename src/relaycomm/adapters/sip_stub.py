"""Documented SIP adapter stub for Asterisk ARI and FreeSWITCH ESL.

This stub builds exact Asterisk ARI or FreeSWITCH ESL origination payloads so
you can connect a self-hosted PBX + SIP trunk without rewriting Relaycomm's API.
Unless `execute=True` is passed against a reachable ARI server, it returns an
honest dry-run / not-connected payload rather than faking PSTN delivery.
"""

from __future__ import annotations

from typing import Any

import httpx

from relaycomm.adapters.base import AdapterCallResult, AdapterSmsResult, CarrierAdapter


class SipStubAdapter(CarrierAdapter):
    name = "sip_stub"
    description = (
        "Documented SIP/PBX adapter stub (Asterisk ARI / FreeSWITCH ESL). "
        "Generates exact originate payloads; requires a live PBX + SIP trunk for real PSTN."
    )
    is_live_pstn = False

    def __init__(
        self,
        *,
        engine: str = "asterisk_ari",
        endpoint: str = "http://localhost:8088/ari",
        trunk: str = "trunk-primary",
    ) -> None:
        self.engine = engine
        self.endpoint = endpoint.rstrip("/")
        self.trunk = trunk

    def build_asterisk_ari_payload(
        self,
        *,
        call_sid: str,
        to_number: str,
        from_number: str,
        twiml_url: str | None,
    ) -> dict[str, Any]:
        return {
            "endpoint": f"PJSIP/{to_number}@{self.trunk}",
            "callerId": from_number,
            "app": "relaycomm-stasis",
            "appArgs": call_sid,
            "variables": {
                "RELAYCOMM_CALL_SID": call_sid,
                "RELAYCOMM_TWIML_URL": twiml_url or "",
            },
        }

    def build_freeswitch_esl_command(
        self,
        *,
        call_sid: str,
        to_number: str,
        from_number: str,
        twiml_url: str | None,
    ) -> str:
        return (
            f"originate {{origination_caller_id_number={from_number},"
            f"relaycomm_call_sid={call_sid},"
            f"relaycomm_twiml_url='{twiml_url or ''}'}}"
            f"sofia/gateway/{self.trunk}/{to_number} &park()"
        )

    async def send_sms(
        self,
        *,
        message_sid: str,
        to_number: str,
        from_number: str,
        body: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterSmsResult:
        sip_message_payload = {
            "engine": self.engine,
            "method": "SIP MESSAGE",
            "from": f"sip:{from_number}@{self.trunk}",
            "to": f"sip:{to_number}@{self.trunk}",
            "body": body,
            "note": "SIP SIMPLE MESSAGE stub; connect Asterisk PJSIP or Kamailio to deliver.",
        }
        return AdapterSmsResult(
            status="queued",
            provider_message_id=f"sip_stub_{message_sid}",
            metadata={"dry_run": True, "sip_payload": sip_message_payload},
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
        opts = options or {}
        ari_payload = self.build_asterisk_ari_payload(
            call_sid=call_sid,
            to_number=to_number,
            from_number=from_number,
            twiml_url=url,
        )
        esl_command = self.build_freeswitch_esl_command(
            call_sid=call_sid,
            to_number=to_number,
            from_number=from_number,
            twiml_url=url,
        )

        if opts.get("execute") and self.engine == "asterisk_ari":
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.post(
                        f"{self.endpoint}/channels",
                        json=ari_payload,
                    )
                    if resp.status_code < 400:
                        return AdapterCallResult(
                            status="ringing",
                            duration="0",
                            twiml_log=[{"verb": "SipOriginate", "ari": ari_payload}],
                            metadata={"ari_response": resp.json()},
                        )
                    return AdapterCallResult(
                        status="failed",
                        error_message=f"Asterisk ARI HTTP {resp.status_code}: {resp.text}",
                        metadata={"ari_payload": ari_payload, "esl_command": esl_command},
                    )
            except Exception as exc:
                return AdapterCallResult(
                    status="failed",
                    error_message=f"Asterisk ARI unreachable ({self.endpoint}): {exc}",
                    metadata={"ari_payload": ari_payload, "esl_command": esl_command},
                )

        return AdapterCallResult(
            status="queued",
            duration="0",
            twiml_log=[
                {
                    "verb": "SipStubOriginate",
                    "engine": self.engine,
                    "endpoint": self.endpoint,
                    "trunk": self.trunk,
                    "ari_payload": ari_payload,
                    "esl_command": esl_command,
                }
            ],
            metadata={
                "dry_run": True,
                "ari_payload": ari_payload,
                "esl_command": esl_command,
            },
        )
