"""FastAPI application implementing Relaycomm's Twilio-compatible REST API and Web Console."""

from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from relaycomm import __version__
from relaycomm.adapters.registry import build_adapters, get_adapter, list_adapters
from relaycomm.config import Settings
from relaycomm.db import Database, utc_now_rfc2822
from relaycomm.security import constant_time_equals, generate_sid
from relaycomm.twiml import TwimlInterpreter
from relaycomm.webhooks import WebhookDispatcher

STATIC_DIR = Path(__file__).parent / "static"
security = HTTPBasic(auto_error=False)


def _twilio_error(status_code: int, code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "more_info": "https://github.com/relaycomm/relaycomm#error-codes",
            "status": status_code,
        },
        headers={"WWW-Authenticate": 'Basic realm="Relaycomm API"'}
        if status_code == 401
        else None,
    )


def _format_message_resource(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sid": row["sid"],
        "account_sid": row["account_sid"],
        "to": row["to_number"],
        "from": row["from_number"],
        "body": row["body"],
        "status": row["status"],
        "direction": row["direction"],
        "num_segments": row["num_segments"],
        "num_media": row["num_media"],
        "price": row["price"],
        "price_unit": row["price_unit"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "api_version": "2010-04-01",
        "uri": row["uri"],
        "subresource_uris": {"media": row["uri"].replace(".json", "/Media.json")},
        "date_created": row["date_created"],
        "date_updated": row["date_updated"],
        "date_sent": row["date_sent"],
    }


def _format_call_resource(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sid": row["sid"],
        "account_sid": row["account_sid"],
        "to": row["to_number"],
        "from": row["from_number"],
        "status": row["status"],
        "direction": row["direction"],
        "duration": row["duration"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "price": None,
        "price_unit": "USD",
        "api_version": "2010-04-01",
        "uri": row["uri"],
        "subresource_uris": {
            "recordings": row["uri"].replace(".json", "/Recordings.json"),
            "trace": row["uri"].replace(".json", "/Trace.json"),
        },
        "date_created": row["date_created"],
        "date_updated": row["date_updated"],
    }


def _format_recording_resource(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sid": row["sid"],
        "account_sid": row["account_sid"],
        "call_sid": row["call_sid"],
        "duration": row["duration"],
        "status": row["status"],
        "channels": row["channels"],
        "source": row["source"],
        "api_version": "2010-04-01",
        "uri": row["uri"],
        "date_created": row["date_created"],
        "date_updated": row["date_updated"],
    }


async def _parse_payload(request: Request) -> dict[str, Any]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        return dict(body) if isinstance(body, dict) else {}
    form = await request.form()
    return {k: v for k, v in form.items()}


def _estimate_segments(body: str) -> str:
    if not body:
        return "1"
    if len(body) <= 160:
        return "1"
    return str(math.ceil(len(body) / 153))


def create_app(
    settings: Settings | None = None,
    *,
    webhook_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    db = Database(cfg.db_path)
    db.ensure_account(cfg.account_sid, cfg.auth_token)
    webhooks = WebhookDispatcher(
        db,
        timeout=cfg.webhook_timeout_seconds,
        client_factory=webhook_client_factory,
    )
    interpreter = TwimlInterpreter(db=db, webhooks=webhooks, base_url=cfg.base_url)
    adapters = build_adapters(cfg, interpreter)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        db.close()

    app = FastAPI(
        title="Relaycomm",
        version=__version__,
        description="Self-hosted, Twilio-compatible programmable SMS and Voice API.",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.db = db
    app.state.webhooks = webhooks
    app.state.interpreter = interpreter
    app.state.adapters = adapters

    def require_account(
        account_sid: str,
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> dict[str, Any]:
        if credentials is None:
            raise _twilio_error(401, 20003, "Authenticate")
        account = db.get_account(credentials.username)
        if account is None or not constant_time_equals(
            credentials.password, account["auth_token"]
        ):
            raise _twilio_error(401, 20003, "Authenticate")
        if not constant_time_equals(account_sid, account["sid"]):
            raise _twilio_error(
                403,
                20003,
                "Authenticated AccountSid does not match path AccountSid",
            )
        return account

    @app.exception_handler(HTTPException)
    async def twilio_http_exception_handler(_req: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "status": exc.status_code},
            headers=exc.headers,
        )

    # --- Health & Web Console ---

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "relaycomm",
            "version": __version__,
            "adapter": cfg.adapter,
            "account_sid": cfg.account_sid,
        }

    @app.get("/", response_class=HTMLResponse)
    async def console_index() -> HTMLResponse:
        html_path = STATIC_DIR / "index.html"
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    # --- Accounts ---

    @app.get("/2010-04-01/Accounts/{account_sid}.json")
    async def get_account(account: dict[str, Any] = Depends(require_account)) -> dict[str, Any]:
        return {
            "sid": account["sid"],
            "friendly_name": account["friendly_name"],
            "status": account["status"],
            "type": "Full",
            "uri": f"/2010-04-01/Accounts/{account['sid']}.json",
            "date_created": account["date_created"],
            "date_updated": account["date_updated"],
        }

    # --- Messages ---

    async def _send_outbound_sms(
        *,
        account: dict[str, Any],
        to_number: str,
        from_number: str,
        body: str,
        status_callback: str | None = None,
        adapter_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            adapter = get_adapter(adapters, adapter_name, cfg.adapter)
        except ValueError as exc:
            raise _twilio_error(400, 21211, str(exc)) from exc

        sid = generate_sid("SM")
        now = utc_now_rfc2822()
        uri = f"/2010-04-01/Accounts/{account['sid']}/Messages/{sid}.json"

        row = db.create_message(
            {
                "sid": sid,
                "account_sid": account["sid"],
                "to_number": to_number,
                "from_number": from_number,
                "body": body,
                "status": "queued",
                "direction": "outbound-api",
                "adapter": adapter.name,
                "num_segments": _estimate_segments(body),
                "num_media": "0",
                "price": "0.0000" if not adapter.is_live_pstn else None,
                "price_unit": "USD",
                "status_callback": status_callback,
                "uri": uri,
                "date_created": now,
                "date_updated": now,
                "metadata": {"adapter": adapter.name},
            }
        )

        result = await adapter.send_sms(
            message_sid=sid,
            to_number=to_number,
            from_number=from_number,
            body=body,
            options=options,
        )

        sent_ts = utc_now_rfc2822() if result.status in {"sent", "delivered"} else None
        updated = db.update_message_status(
            sid,
            result.status,
            date_sent=sent_ts,
            error_code=result.error_code,
            error_message=result.error_message,
            metadata={
                "provider_message_id": result.provider_message_id,
                **result.metadata,
            },
        )
        row = updated or row

        if status_callback:
            await webhooks.dispatch(
                account_sid=account["sid"],
                auth_token=account["auth_token"],
                resource_sid=sid,
                event=f"message.{row['status']}",
                url=status_callback,
                method="POST",
                base_url=cfg.base_url,
                params={
                    "MessageSid": sid,
                    "SmsSid": sid,
                    "AccountSid": account["sid"],
                    "From": from_number,
                    "To": to_number,
                    "Body": body,
                    "MessageStatus": row["status"],
                    "SmsStatus": row["status"],
                    "ApiVersion": "2010-04-01",
                },
            )

        return row

    @app.post(
        "/2010-04-01/Accounts/{account_sid}/Messages.json",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_message(
        request: Request,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        payload = await _parse_payload(request)
        to_number = str(payload.get("To", "")).strip()
        from_number = str(payload.get("From", "")).strip()
        body = str(payload.get("Body", ""))
        status_callback = payload.get("StatusCallback")
        adapter_name = payload.get("Adapter")
        options = {
            k: v
            for k, v in {
                "carrier": payload.get("Carrier"),
                "carrier_domain": payload.get("CarrierDomain"),
            }.items()
            if v is not None
        }

        if not to_number:
            raise _twilio_error(400, 21211, "A 'To' phone number is required.")
        if not from_number:
            raise _twilio_error(400, 21212, "A 'From' phone number is required.")
        if not body and not payload.get("MediaUrl"):
            raise _twilio_error(400, 21602, "Message body is required.")

        row = await _send_outbound_sms(
            account=account,
            to_number=to_number,
            from_number=from_number,
            body=body,
            status_callback=str(status_callback) if status_callback else None,
            adapter_name=str(adapter_name) if adapter_name else None,
            options=options,
        )
        return _format_message_resource(row)

    @app.get("/2010-04-01/Accounts/{account_sid}/Messages.json")
    async def list_messages(
        account: dict[str, Any] = Depends(require_account),
        To: str | None = Query(default=None),
        From: str | None = Query(default=None),
        PageSize: int = Query(default=50, ge=1, le=200),
        Page: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = db.list_messages(
            account["sid"],
            to_number=To,
            from_number=From,
            limit=PageSize,
            offset=Page * PageSize,
        )
        return {
            "messages": [_format_message_resource(r) for r in rows],
            "page": Page,
            "page_size": PageSize,
            "uri": f"/2010-04-01/Accounts/{account['sid']}/Messages.json?PageSize={PageSize}&Page={Page}",
        }

    @app.get("/2010-04-01/Accounts/{account_sid}/Messages/{message_sid}.json")
    async def get_message(
        message_sid: str,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        row = db.get_message(account["sid"], message_sid)
        if row is None:
            raise _twilio_error(404, 20404, f"Message {message_sid} was not found")
        return _format_message_resource(row)

    @app.delete(
        "/2010-04-01/Accounts/{account_sid}/Messages/{message_sid}.json",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_message(
        message_sid: str,
        account: dict[str, Any] = Depends(require_account),
    ) -> Response:
        deleted = db.delete_message(account["sid"], message_sid)
        if not deleted:
            raise _twilio_error(404, 20404, f"Message {message_sid} was not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # --- Calls ---

    async def _place_outbound_call(
        *,
        account: dict[str, Any],
        to_number: str,
        from_number: str,
        url: str | None = None,
        twiml: str | None = None,
        method: str = "POST",
        status_callback: str | None = None,
        adapter_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            adapter = get_adapter(adapters, adapter_name, cfg.adapter)
        except ValueError as exc:
            raise _twilio_error(400, 21211, str(exc)) from exc

        sid = generate_sid("CA")
        now = utc_now_rfc2822()
        uri = f"/2010-04-01/Accounts/{account['sid']}/Calls/{sid}.json"

        row = db.create_call(
            {
                "sid": sid,
                "account_sid": account["sid"],
                "to_number": to_number,
                "from_number": from_number,
                "url": url,
                "twiml": twiml,
                "method": method.upper(),
                "status": "queued",
                "direction": "outbound-api",
                "adapter": adapter.name,
                "duration": "0",
                "status_callback": status_callback,
                "uri": uri,
                "date_created": now,
                "date_updated": now,
                "start_time": now,
                "metadata": {"adapter": adapter.name},
            }
        )

        result = await adapter.place_call(
            call_sid=sid,
            account_sid=account["sid"],
            auth_token=account["auth_token"],
            to_number=to_number,
            from_number=from_number,
            url=url,
            twiml=twiml,
            method=method.upper(),
            options=options,
        )

        end_ts = utc_now_rfc2822() if result.status in {"completed", "failed"} else None
        updated = db.update_call(
            sid,
            status=result.status,
            duration=result.duration,
            end_time=end_ts,
            twiml_log=result.twiml_log,
            metadata={
                "error_message": result.error_message,
                **result.metadata,
            },
        )
        row = updated or row

        if status_callback:
            await webhooks.dispatch(
                account_sid=account["sid"],
                auth_token=account["auth_token"],
                resource_sid=sid,
                event=f"call.{row['status']}",
                url=status_callback,
                method="POST",
                base_url=cfg.base_url,
                params={
                    "CallSid": sid,
                    "AccountSid": account["sid"],
                    "From": from_number,
                    "To": to_number,
                    "CallStatus": row["status"],
                    "CallDuration": row["duration"] or "0",
                    "Direction": "outbound-api",
                    "ApiVersion": "2010-04-01",
                },
            )

        return row

    @app.post(
        "/2010-04-01/Accounts/{account_sid}/Calls.json",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_call(
        request: Request,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        payload = await _parse_payload(request)
        to_number = str(payload.get("To", "")).strip()
        from_number = str(payload.get("From", "")).strip()
        url = payload.get("Url")
        twiml = payload.get("Twiml")
        method = str(payload.get("Method", "POST")).upper()
        status_callback = payload.get("StatusCallback")
        adapter_name = payload.get("Adapter")
        options = {
            k: v
            for k, v in {
                "simulated_digits": payload.get("SimulatedDigits"),
                "simulated_recording_note": payload.get("SimulatedRecordingNote"),
                "connection_id": payload.get("ConnectionId"),
                "execute": payload.get("Execute"),
            }.items()
            if v is not None
        }

        if not to_number:
            raise _twilio_error(400, 21211, "A 'To' phone number is required.")
        if not from_number:
            raise _twilio_error(400, 21212, "A 'From' phone number is required.")
        if not url and not twiml:
            raise _twilio_error(400, 21205, "Either 'Url' or 'Twiml' is required.")

        row = await _place_outbound_call(
            account=account,
            to_number=to_number,
            from_number=from_number,
            url=str(url) if url else None,
            twiml=str(twiml) if twiml else None,
            method=method,
            status_callback=str(status_callback) if status_callback else None,
            adapter_name=str(adapter_name) if adapter_name else None,
            options=options,
        )
        return _format_call_resource(row)

    @app.get("/2010-04-01/Accounts/{account_sid}/Calls.json")
    async def list_calls(
        account: dict[str, Any] = Depends(require_account),
        To: str | None = Query(default=None),
        From: str | None = Query(default=None),
        Status: str | None = Query(default=None),
        PageSize: int = Query(default=50, ge=1, le=200),
        Page: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = db.list_calls(
            account["sid"],
            to_number=To,
            from_number=From,
            status=Status,
            limit=PageSize,
            offset=Page * PageSize,
        )
        return {
            "calls": [_format_call_resource(r) for r in rows],
            "page": Page,
            "page_size": PageSize,
            "uri": f"/2010-04-01/Accounts/{account['sid']}/Calls.json?PageSize={PageSize}&Page={Page}",
        }

    @app.get("/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json")
    async def get_call(
        call_sid: str,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        row = db.get_call(account["sid"], call_sid)
        if row is None:
            raise _twilio_error(404, 20404, f"Call {call_sid} was not found")
        return _format_call_resource(row)

    @app.post("/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json")
    async def update_call(
        call_sid: str,
        request: Request,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        existing = db.get_call(account["sid"], call_sid)
        if existing is None:
            raise _twilio_error(404, 20404, f"Call {call_sid} was not found")
        payload = await _parse_payload(request)
        new_status = str(payload.get("Status", existing["status"]))
        end_time = utc_now_rfc2822() if new_status in {"completed", "canceled"} else None
        updated = db.update_call(call_sid, status=new_status, end_time=end_time)
        return _format_call_resource(updated or existing)

    @app.get("/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}/Trace.json")
    async def get_call_trace(
        call_sid: str,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        row = db.get_call(account["sid"], call_sid)
        if row is None:
            raise _twilio_error(404, 20404, f"Call {call_sid} was not found")
        return {
            "call_sid": call_sid,
            "status": row["status"],
            "duration": row["duration"],
            "twiml_log": json.loads(row["twiml_log_json"] or "[]"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @app.get("/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}/Recordings.json")
    async def list_call_recordings(
        call_sid: str,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        call = db.get_call(account["sid"], call_sid)
        if call is None:
            raise _twilio_error(404, 20404, f"Call {call_sid} was not found")
        recs = db.list_call_recordings(account["sid"], call_sid)
        return {
            "recordings": [_format_recording_resource(r) for r in recs],
            "uri": f"/2010-04-01/Accounts/{account['sid']}/Calls/{call_sid}/Recordings.json",
        }

    @app.get(
        "/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}/Recordings/{recording_sid}.json"
    )
    async def get_recording(
        call_sid: str,
        recording_sid: str,
        account: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        rec = db.get_recording(account["sid"], recording_sid)
        if rec is None or rec["call_sid"] != call_sid:
            raise _twilio_error(404, 20404, f"Recording {recording_sid} was not found")
        return _format_recording_resource(rec)

    # --- Local Web Console API & Interactive Simulator ---

    @app.get("/console/api/state")
    async def console_state() -> dict[str, Any]:
        account = db.get_account(cfg.account_sid)
        messages = db.list_messages(cfg.account_sid, limit=50)
        calls = db.list_calls(cfg.account_sid, limit=50)
        webhooks_list = db.list_webhook_deliveries(cfg.account_sid, limit=50)
        return {
            "service": "Relaycomm",
            "version": __version__,
            "account_sid": cfg.account_sid,
            "active_adapter": cfg.adapter,
            "base_url": cfg.base_url,
            "stats": db.stats(cfg.account_sid),
            "adapters": list_adapters(adapters),
            "messages": [
                {
                    **_format_message_resource(m),
                    "adapter": m["adapter"],
                    "metadata": json.loads(m["metadata_json"] or "{}"),
                }
                for m in messages
            ],
            "calls": [
                {
                    **_format_call_resource(c),
                    "adapter": c["adapter"],
                    "twiml_log": json.loads(c["twiml_log_json"] or "[]"),
                    "metadata": json.loads(c["metadata_json"] or "{}"),
                    "recordings": db.list_call_recordings(cfg.account_sid, c["sid"]),
                }
                for c in calls
            ],
            "webhooks": [
                {
                    "id": w["id"],
                    "resource_sid": w["resource_sid"],
                    "event": w["event"],
                    "target_url": w["target_url"],
                    "method": w["method"],
                    "request_body": json.loads(w["request_body_json"] or "{}"),
                    "signature": w["signature"],
                    "status_code": w["status_code"],
                    "response_snippet": w["response_snippet"],
                    "error": w["error"],
                    "created_at": w["created_at"],
                }
                for w in webhooks_list
            ],
            "account_status": account["status"] if account else "unknown",
        }

    @app.post("/console/api/simulate/outbound-sms", status_code=201)
    async def console_send_sms(request: Request) -> dict[str, Any]:
        payload = await _parse_payload(request)
        account = db.get_account(cfg.account_sid)
        assert account is not None
        to_number = str(payload.get("to", "+15550109999")).strip()
        from_number = str(payload.get("from", "+15550100001")).strip()
        body = str(payload.get("body", "")).strip()
        if not body:
            raise HTTPException(status_code=400, detail="body is required")
        status_callback = payload.get("status_callback") or None
        adapter_name = payload.get("adapter") or None
        row = await _send_outbound_sms(
            account=account,
            to_number=to_number,
            from_number=from_number,
            body=body,
            status_callback=str(status_callback) if status_callback else None,
            adapter_name=str(adapter_name) if adapter_name else None,
        )
        return _format_message_resource(row)

    @app.post("/console/api/simulate/outbound-call", status_code=201)
    async def console_make_call(request: Request) -> dict[str, Any]:
        payload = await _parse_payload(request)
        account = db.get_account(cfg.account_sid)
        assert account is not None
        to_number = str(payload.get("to", "+15550109999")).strip()
        from_number = str(payload.get("from", "+15550100001")).strip()
        twiml = payload.get("twiml")
        url = payload.get("url")
        if not twiml and not url:
            twiml = "<Response><Say>Hello from Relaycomm simulator.</Say><Hangup/></Response>"
        row = await _place_outbound_call(
            account=account,
            to_number=to_number,
            from_number=from_number,
            url=str(url) if url else None,
            twiml=str(twiml) if twiml else None,
            status_callback=str(payload["status_callback"])
            if payload.get("status_callback")
            else None,
            adapter_name=str(payload["adapter"]) if payload.get("adapter") else None,
            options={"simulated_digits": payload.get("simulated_digits")},
        )
        return {
            **_format_call_resource(row),
            "twiml_log": json.loads(row["twiml_log_json"] or "[]"),
        }

    @app.post("/console/api/simulate/inbound-sms", status_code=201)
    async def simulate_inbound_sms(request: Request) -> dict[str, Any]:
        """Simulate an incoming SMS arriving at a Relaycomm number and invoking a webhook."""
        payload = await _parse_payload(request)
        account = db.get_account(cfg.account_sid)
        assert account is not None

        from_number = str(payload.get("From") or payload.get("from") or "+15550108888")
        to_number = str(payload.get("To") or payload.get("to") or "+15550100001")
        body = str(payload.get("Body") or payload.get("body") or "Hello Relaycomm")
        webhook_url = payload.get("WebhookUrl") or payload.get("webhook_url")

        sid = generate_sid("SM")
        now = utc_now_rfc2822()
        uri = f"/2010-04-01/Accounts/{account['sid']}/Messages/{sid}.json"
        row = db.create_message(
            {
                "sid": sid,
                "account_sid": account["sid"],
                "to_number": to_number,
                "from_number": from_number,
                "body": body,
                "status": "received",
                "direction": "inbound",
                "adapter": "simulator",
                "num_segments": _estimate_segments(body),
                "uri": uri,
                "date_created": now,
                "date_updated": now,
                "date_sent": now,
                "metadata": {"simulated_inbound": True},
            }
        )

        webhook_result = None
        if webhook_url:
            resp = await webhooks.dispatch(
                account_sid=account["sid"],
                auth_token=account["auth_token"],
                resource_sid=sid,
                event="sms.inbound",
                url=str(webhook_url),
                method="POST",
                base_url=cfg.base_url,
                params={
                    "MessageSid": sid,
                    "SmsSid": sid,
                    "AccountSid": account["sid"],
                    "From": from_number,
                    "To": to_number,
                    "Body": body,
                    "NumMedia": "0",
                    "SmsStatus": "received",
                    "ApiVersion": "2010-04-01",
                },
            )
            webhook_result = {
                "status_code": resp.status_code,
                "signature": resp.signature,
                "response": resp.text,
                "error": resp.error,
            }

        return {"message": _format_message_resource(row), "webhook": webhook_result}

    @app.post("/console/api/simulate/inbound-call", status_code=201)
    async def simulate_inbound_call(request: Request) -> dict[str, Any]:
        """Simulate an incoming voice call that fetches TwiML from WebhookUrl."""
        payload = await _parse_payload(request)
        account = db.get_account(cfg.account_sid)
        assert account is not None

        from_number = str(payload.get("From") or payload.get("from") or "+15550108888")
        to_number = str(payload.get("To") or payload.get("to") or "+15550100001")
        webhook_url = payload.get("WebhookUrl") or payload.get("webhook_url")
        simulated_digits = payload.get("SimulatedDigits") or payload.get("simulated_digits")

        if not webhook_url:
            raise HTTPException(status_code=400, detail="WebhookUrl is required")

        sid = generate_sid("CA")
        now = utc_now_rfc2822()
        uri = f"/2010-04-01/Accounts/{account['sid']}/Calls/{sid}.json"
        db.create_call(
            {
                "sid": sid,
                "account_sid": account["sid"],
                "to_number": to_number,
                "from_number": from_number,
                "url": str(webhook_url),
                "method": "POST",
                "status": "in-progress",
                "direction": "inbound",
                "adapter": "simulator",
                "duration": "0",
                "uri": uri,
                "date_created": now,
                "date_updated": now,
                "start_time": now,
                "metadata": {"simulated_inbound": True},
            }
        )

        exec_res = await interpreter.execute_from_url(
            url=str(webhook_url),
            method="POST",
            call_sid=sid,
            account_sid=account["sid"],
            auth_token=account["auth_token"],
            from_number=from_number,
            to_number=to_number,
            direction="inbound",
            simulated_digits=str(simulated_digits) if simulated_digits else None,
        )

        updated = db.update_call(
            sid,
            status=exec_res.final_status,
            duration=str(max(1, exec_res.duration_seconds)),
            end_time=utc_now_rfc2822(),
            twiml_log=exec_res.to_log_dicts(),
            metadata={"errors": exec_res.errors},
        )
        assert updated is not None
        return {
            **_format_call_resource(updated),
            "twiml_log": exec_res.to_log_dicts(),
        }

    return app


def main() -> None:
    cfg = Settings.from_env()
    uvicorn.run(
        create_app(cfg),
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
