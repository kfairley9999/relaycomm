"""Built-in offline carrier simulator (default adapter)."""

from __future__ import annotations

from typing import Any

from relaycomm.adapters.base import AdapterCallResult, AdapterSmsResult, CarrierAdapter
from relaycomm.twiml import TwimlInterpreter


class SimulatorAdapter(CarrierAdapter):
    name = "simulator"
    description = (
        "Offline built-in simulator. Records SMS deliveries and executes TwiML "
        "voice flows locally in SQLite with zero external carrier calls."
    )
    is_live_pstn = False

    def __init__(self, interpreter: TwimlInterpreter) -> None:
        self.interpreter = interpreter

    async def send_sms(
        self,
        *,
        message_sid: str,
        to_number: str,
        from_number: str,
        body: str,
        options: dict[str, Any] | None = None,
    ) -> AdapterSmsResult:
        return AdapterSmsResult(
            status="delivered",
            provider_message_id=f"sim_{message_sid}",
            metadata={
                "simulated": True,
                "to": to_number,
                "from": from_number,
                "char_count": len(body),
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
        opts = options or {}
        simulated_digits = opts.get("simulated_digits")
        simulated_recording_note = opts.get("simulated_recording_note")

        if twiml:
            exec_res = await self.interpreter.execute_xml(
                xml_text=twiml,
                call_sid=call_sid,
                account_sid=account_sid,
                auth_token=auth_token,
                from_number=from_number,
                to_number=to_number,
                simulated_digits=simulated_digits,
                simulated_recording_note=simulated_recording_note,
            )
        elif url:
            exec_res = await self.interpreter.execute_from_url(
                url=url,
                method=method,
                call_sid=call_sid,
                account_sid=account_sid,
                auth_token=auth_token,
                from_number=from_number,
                to_number=to_number,
                simulated_digits=simulated_digits,
                simulated_recording_note=simulated_recording_note,
            )
        else:
            return AdapterCallResult(
                status="completed",
                duration="1",
                twiml_log=[{"verb": "NoOp", "note": "No Url or Twiml provided"}],
                metadata={"simulated": True},
            )

        return AdapterCallResult(
            status=exec_res.final_status,
            duration=str(max(1, exec_res.duration_seconds)),
            twiml_log=exec_res.to_log_dicts(),
            error_message="; ".join(exec_res.errors) if exec_res.errors else None,
            metadata={
                "simulated": True,
                "recordings": [r["sid"] for r in exec_res.recordings],
            },
        )
