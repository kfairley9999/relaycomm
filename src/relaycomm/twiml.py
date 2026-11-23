"""TwiML-compatible voice XML interpreter running against the Relaycomm simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import defusedxml.ElementTree as ET

from relaycomm.db import Database, utc_now_rfc2822
from relaycomm.security import generate_sid
from relaycomm.webhooks import WebhookDispatcher


@dataclass
class TwimlStep:
    verb: str
    detail: dict[str, Any]


@dataclass
class TwimlExecutionResult:
    steps: list[TwimlStep] = field(default_factory=list)
    duration_seconds: int = 0
    recordings: list[dict[str, Any]] = field(default_factory=list)
    final_status: str = "completed"
    errors: list[str] = field(default_factory=list)

    def to_log_dicts(self) -> list[dict[str, Any]]:
        return [{"verb": s.verb, **s.detail} for s in self.steps]


class TwimlInterpreter:
    """Executes TwiML <Response> documents safely using defusedxml."""

    MAX_REDIRECT_DEPTH = 10

    def __init__(
        self,
        *,
        db: Database,
        webhooks: WebhookDispatcher,
        base_url: str,
    ) -> None:
        self.db = db
        self.webhooks = webhooks
        self.base_url = base_url

    async def execute_from_url(
        self,
        *,
        url: str,
        method: str,
        call_sid: str,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        direction: str = "outbound-api",
        simulated_digits: str | None = None,
        simulated_recording_note: str | None = None,
    ) -> TwimlExecutionResult:
        result = TwimlExecutionResult()
        resp = await self.webhooks.dispatch(
            account_sid=account_sid,
            auth_token=auth_token,
            resource_sid=call_sid,
            event="twiml.fetch",
            url=url,
            method=method,
            base_url=self.base_url,
            params={
                "CallSid": call_sid,
                "AccountSid": account_sid,
                "From": from_number,
                "To": to_number,
                "CallStatus": "in-progress",
                "Direction": direction,
                "ApiVersion": "2010-04-01",
            },
        )
        if resp.error or not resp.status_code or resp.status_code >= 400:
            err = resp.error or f"HTTP {resp.status_code} fetching TwiML from {url}"
            result.errors.append(err)
            result.final_status = "failed"
            result.steps.append(
                TwimlStep("Error", {"stage": "twiml.fetch", "url": url, "error": err})
            )
            return result

        await self._execute_xml(
            xml_text=resp.text,
            current_url=url,
            call_sid=call_sid,
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
            to_number=to_number,
            direction=direction,
            simulated_digits=simulated_digits,
            simulated_recording_note=simulated_recording_note,
            result=result,
            depth=0,
        )
        return result

    async def execute_xml(
        self,
        *,
        xml_text: str,
        call_sid: str,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        direction: str = "outbound-api",
        simulated_digits: str | None = None,
        simulated_recording_note: str | None = None,
        current_url: str = "",
    ) -> TwimlExecutionResult:
        result = TwimlExecutionResult()
        await self._execute_xml(
            xml_text=xml_text,
            current_url=current_url,
            call_sid=call_sid,
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
            to_number=to_number,
            direction=direction,
            simulated_digits=simulated_digits,
            simulated_recording_note=simulated_recording_note,
            result=result,
            depth=0,
        )
        return result

    async def _execute_xml(
        self,
        *,
        xml_text: str,
        current_url: str,
        call_sid: str,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        direction: str,
        simulated_digits: str | None,
        simulated_recording_note: str | None,
        result: TwimlExecutionResult,
        depth: int,
    ) -> None:
        if depth > self.MAX_REDIRECT_DEPTH:
            msg = f"Max TwiML redirect/action depth ({self.MAX_REDIRECT_DEPTH}) exceeded"
            result.errors.append(msg)
            result.final_status = "failed"
            result.steps.append(TwimlStep("Error", {"error": msg}))
            return

        try:
            root = ET.fromstring(xml_text.strip())
        except Exception as exc:
            msg = f"Invalid TwiML XML: {exc}"
            result.errors.append(msg)
            result.final_status = "failed"
            result.steps.append(TwimlStep("Error", {"error": msg}))
            return

        if root.tag != "Response":
            msg = f"Root TwiML element must be <Response>, got <{root.tag}>"
            result.errors.append(msg)
            result.final_status = "failed"
            result.steps.append(TwimlStep("Error", {"error": msg}))
            return

        for elem in list(root):
            tag = elem.tag

            if tag == "Say":
                text = "".join(elem.itertext()).strip()
                loop = int(elem.attrib.get("loop", "1"))
                voice = elem.attrib.get("voice", "alice")
                language = elem.attrib.get("language", "en-US")
                word_count = max(1, len(text.split()))
                dur = max(1, (word_count // 2) * max(1, loop))
                result.duration_seconds += dur
                result.steps.append(
                    TwimlStep(
                        "Say",
                        {
                            "text": text,
                            "voice": voice,
                            "language": language,
                            "loop": loop,
                            "duration": dur,
                        },
                    )
                )

            elif tag == "Play":
                media_url = (elem.text or "").strip()
                loop = int(elem.attrib.get("loop", "1"))
                dur = 3 * max(1, loop)
                result.duration_seconds += dur
                result.steps.append(
                    TwimlStep(
                        "Play",
                        {"url": media_url, "loop": loop, "duration": dur},
                    )
                )

            elif tag == "Pause":
                length = int(elem.attrib.get("length", "1"))
                result.duration_seconds += max(0, length)
                result.steps.append(TwimlStep("Pause", {"length": length}))

            elif tag == "Gather":
                input_type = elem.attrib.get("input", "dtmf")
                timeout = int(elem.attrib.get("timeout", "5"))
                num_digits = elem.attrib.get("numDigits")
                finish_on_key = elem.attrib.get("finishOnKey", "#")
                action = elem.attrib.get("action")
                action_method = elem.attrib.get("method", "POST").upper()

                nested_prompts: list[dict[str, Any]] = []
                for child in list(elem):
                    if child.tag == "Say":
                        prompt_text = "".join(child.itertext()).strip()
                        nested_prompts.append(
                            {
                                "verb": "Say",
                                "text": prompt_text,
                                "voice": child.attrib.get("voice", "alice"),
                            }
                        )
                        result.duration_seconds += max(1, len(prompt_text.split()) // 2)
                    elif child.tag == "Play":
                        nested_prompts.append(
                            {"verb": "Play", "url": (child.text or "").strip()}
                        )
                        result.duration_seconds += 3

                digits = (simulated_digits or "").strip()
                if num_digits and digits:
                    digits = digits[: int(num_digits)]
                if finish_on_key and digits.endswith(finish_on_key):
                    digits = digits[: -len(finish_on_key)]

                result.duration_seconds += 1 if digits else timeout
                result.steps.append(
                    TwimlStep(
                        "Gather",
                        {
                            "input": input_type,
                            "timeout": timeout,
                            "numDigits": num_digits,
                            "action": action,
                            "method": action_method,
                            "prompts": nested_prompts,
                            "digits_received": digits or None,
                        },
                    )
                )

                if action and digits:
                    next_url = (
                        urljoin(current_url, action)
                        if current_url and not action.startswith(("http://", "https://", "/"))
                        else action
                    )
                    resp = await self.webhooks.dispatch(
                        account_sid=account_sid,
                        auth_token=auth_token,
                        resource_sid=call_sid,
                        event="twiml.gather",
                        url=next_url,
                        method=action_method,
                        base_url=self.base_url,
                        params={
                            "CallSid": call_sid,
                            "AccountSid": account_sid,
                            "From": from_number,
                            "To": to_number,
                            "CallStatus": "in-progress",
                            "Digits": digits,
                            "ApiVersion": "2010-04-01",
                        },
                    )
                    if resp.error or not resp.status_code or resp.status_code >= 400:
                        err = resp.error or f"HTTP {resp.status_code} on Gather action {next_url}"
                        result.errors.append(err)
                        result.steps.append(TwimlStep("Error", {"error": err}))
                    elif resp.text.strip():
                        # Consume simulated digits once so nested Gather doesn't loop blindly
                        await self._execute_xml(
                            xml_text=resp.text,
                            current_url=next_url,
                            call_sid=call_sid,
                            account_sid=account_sid,
                            auth_token=auth_token,
                            from_number=from_number,
                            to_number=to_number,
                            direction=direction,
                            simulated_digits=None,
                            simulated_recording_note=simulated_recording_note,
                            result=result,
                            depth=depth + 1,
                        )
                        return

            elif tag == "Dial":
                caller_id = elem.attrib.get("callerId", from_number)
                timeout = int(elem.attrib.get("timeout", "30"))
                targets: list[str] = []
                if elem.text and elem.text.strip():
                    targets.append(elem.text.strip())
                for child in list(elem):
                    if child.tag in {"Number", "Sip", "Client"} and child.text:
                        targets.append(f"{child.tag}:{child.text.strip()}")
                result.duration_seconds += 5
                result.steps.append(
                    TwimlStep(
                        "Dial",
                        {
                            "targets": targets,
                            "callerId": caller_id,
                            "timeout": timeout,
                            "simulated_outcome": "answered-and-bridged",
                        },
                    )
                )

            elif tag == "Record":
                max_length = int(elem.attrib.get("maxLength", "3600"))
                play_beep = elem.attrib.get("playBeep", "true").lower() == "true"
                action = elem.attrib.get("action")
                action_method = elem.attrib.get("method", "POST").upper()
                rec_duration = min(max_length, 7)
                rec_sid = generate_sid("RE")
                rec_uri = (
                    f"/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}/Recordings/{rec_sid}.json"
                )
                now = utc_now_rfc2822()
                rec_row = self.db.create_recording(
                    {
                        "sid": rec_sid,
                        "account_sid": account_sid,
                        "call_sid": call_sid,
                        "duration": str(rec_duration),
                        "status": "completed",
                        "channels": 1,
                        "source": "RecordVerb",
                        "uri": rec_uri,
                        "note": simulated_recording_note
                        or "Simulated voice recording captured by Relaycomm",
                        "date_created": now,
                        "date_updated": now,
                    }
                )
                result.recordings.append(rec_row)
                result.duration_seconds += rec_duration
                result.steps.append(
                    TwimlStep(
                        "Record",
                        {
                            "recording_sid": rec_sid,
                            "duration": rec_duration,
                            "playBeep": play_beep,
                            "maxLength": max_length,
                            "action": action,
                        },
                    )
                )
                if action:
                    next_url = (
                        urljoin(current_url, action)
                        if current_url and not action.startswith(("http://", "https://", "/"))
                        else action
                    )
                    resp = await self.webhooks.dispatch(
                        account_sid=account_sid,
                        auth_token=auth_token,
                        resource_sid=call_sid,
                        event="twiml.record",
                        url=next_url,
                        method=action_method,
                        base_url=self.base_url,
                        params={
                            "CallSid": call_sid,
                            "AccountSid": account_sid,
                            "From": from_number,
                            "To": to_number,
                            "RecordingSid": rec_sid,
                            "RecordingUrl": f"{self.base_url}{rec_uri}",
                            "RecordingDuration": str(rec_duration),
                            "ApiVersion": "2010-04-01",
                        },
                    )
                    if resp.text.strip() and resp.status_code and resp.status_code < 400:
                        await self._execute_xml(
                            xml_text=resp.text,
                            current_url=next_url,
                            call_sid=call_sid,
                            account_sid=account_sid,
                            auth_token=auth_token,
                            from_number=from_number,
                            to_number=to_number,
                            direction=direction,
                            simulated_digits=simulated_digits,
                            simulated_recording_note=simulated_recording_note,
                            result=result,
                            depth=depth + 1,
                        )
                        return

            elif tag == "Redirect":
                target = (elem.text or "").strip()
                redir_method = elem.attrib.get("method", "POST").upper()
                result.steps.append(
                    TwimlStep("Redirect", {"url": target, "method": redir_method})
                )
                if target:
                    next_url = (
                        urljoin(current_url, target)
                        if current_url and not target.startswith(("http://", "https://", "/"))
                        else target
                    )
                    resp = await self.webhooks.dispatch(
                        account_sid=account_sid,
                        auth_token=auth_token,
                        resource_sid=call_sid,
                        event="twiml.redirect",
                        url=next_url,
                        method=redir_method,
                        base_url=self.base_url,
                        params={
                            "CallSid": call_sid,
                            "AccountSid": account_sid,
                            "From": from_number,
                            "To": to_number,
                            "CallStatus": "in-progress",
                            "ApiVersion": "2010-04-01",
                        },
                    )
                    if resp.error or not resp.status_code or resp.status_code >= 400:
                        err = resp.error or f"HTTP {resp.status_code} on Redirect {next_url}"
                        result.errors.append(err)
                        result.final_status = "failed"
                        result.steps.append(TwimlStep("Error", {"error": err}))
                        return
                    await self._execute_xml(
                        xml_text=resp.text,
                        current_url=next_url,
                        call_sid=call_sid,
                        account_sid=account_sid,
                        auth_token=auth_token,
                        from_number=from_number,
                        to_number=to_number,
                        direction=direction,
                        simulated_digits=simulated_digits,
                        simulated_recording_note=simulated_recording_note,
                        result=result,
                        depth=depth + 1,
                    )
                return

            elif tag == "Hangup":
                result.steps.append(TwimlStep("Hangup", {"reason": "verb"}))
                result.final_status = "completed"
                return

            else:
                result.steps.append(
                    TwimlStep("UnsupportedVerb", {"tag": tag, "attrib": dict(elem.attrib)})
                )
