"""SQLite storage layer for Relaycomm."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any


def utc_now_rfc2822() -> str:
    """Return current UTC time formatted like Twilio's RFC 2822 date strings."""
    return format_datetime(datetime.now(timezone.utc), usegmt=False)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite repository for accounts, messages, calls, recordings, and webhooks."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    sid TEXT PRIMARY KEY,
                    friendly_name TEXT NOT NULL,
                    auth_token TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    date_created TEXT NOT NULL,
                    date_updated TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    sid TEXT PRIMARY KEY,
                    account_sid TEXT NOT NULL,
                    to_number TEXT NOT NULL,
                    from_number TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    num_segments TEXT NOT NULL DEFAULT '1',
                    num_media TEXT NOT NULL DEFAULT '0',
                    price TEXT,
                    price_unit TEXT NOT NULL DEFAULT 'USD',
                    error_code INTEGER,
                    error_message TEXT,
                    status_callback TEXT,
                    uri TEXT NOT NULL,
                    date_created TEXT NOT NULL,
                    date_updated TEXT NOT NULL,
                    date_sent TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (account_sid) REFERENCES accounts(sid)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_account
                    ON messages(account_sid);

                CREATE TABLE IF NOT EXISTS calls (
                    sid TEXT PRIMARY KEY,
                    account_sid TEXT NOT NULL,
                    to_number TEXT NOT NULL,
                    from_number TEXT NOT NULL,
                    url TEXT,
                    twiml TEXT,
                    method TEXT NOT NULL DEFAULT 'POST',
                    status TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    duration TEXT,
                    status_callback TEXT,
                    status_callback_event TEXT,
                    uri TEXT NOT NULL,
                    date_created TEXT NOT NULL,
                    date_updated TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    twiml_log_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (account_sid) REFERENCES accounts(sid)
                );

                CREATE INDEX IF NOT EXISTS idx_calls_account
                    ON calls(account_sid);

                CREATE TABLE IF NOT EXISTS recordings (
                    sid TEXT PRIMARY KEY,
                    account_sid TEXT NOT NULL,
                    call_sid TEXT NOT NULL,
                    duration TEXT NOT NULL DEFAULT '0',
                    status TEXT NOT NULL DEFAULT 'completed',
                    channels INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'RecordVerb',
                    uri TEXT NOT NULL,
                    note TEXT,
                    date_created TEXT NOT NULL,
                    date_updated TEXT NOT NULL,
                    FOREIGN KEY (account_sid) REFERENCES accounts(sid),
                    FOREIGN KEY (call_sid) REFERENCES calls(sid)
                );

                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_sid TEXT NOT NULL,
                    resource_sid TEXT NOT NULL,
                    event TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    method TEXT NOT NULL DEFAULT 'POST',
                    request_body_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    status_code INTEGER,
                    response_snippet TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_webhooks_created
                    ON webhook_deliveries(id DESC);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ensure_account(
        self, sid: str, auth_token: str, friendly_name: str = "Relaycomm Default Account"
    ) -> dict[str, Any]:
        now = utc_now_rfc2822()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE sid = ?", (sid,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO accounts (sid, friendly_name, auth_token, status, date_created, date_updated)
                    VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (sid, friendly_name, auth_token, now, now),
                )
                self._conn.commit()
            else:
                if row["auth_token"] != auth_token:
                    self._conn.execute(
                        "UPDATE accounts SET auth_token = ?, date_updated = ? WHERE sid = ?",
                        (auth_token, now, sid),
                    )
                    self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE sid = ?", (sid,)
            ).fetchone()
            return dict(row)

    def get_account(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE sid = ?", (sid,)
            ).fetchone()
            return dict(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM accounts ORDER BY rowid ASC").fetchall()
            return [dict(r) for r in rows]

    # --- Messages ---

    def create_message(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO messages (
                    sid, account_sid, to_number, from_number, body, status, direction,
                    adapter, num_segments, num_media, price, price_unit, error_code,
                    error_message, status_callback, uri, date_created, date_updated,
                    date_sent, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["sid"],
                    data["account_sid"],
                    data["to_number"],
                    data["from_number"],
                    data["body"],
                    data["status"],
                    data["direction"],
                    data["adapter"],
                    data.get("num_segments", "1"),
                    data.get("num_media", "0"),
                    data.get("price"),
                    data.get("price_unit", "USD"),
                    data.get("error_code"),
                    data.get("error_message"),
                    data.get("status_callback"),
                    data["uri"],
                    data["date_created"],
                    data["date_updated"],
                    data.get("date_sent"),
                    json.dumps(data.get("metadata", {})),
                ),
            )
            self._conn.commit()
            return self.get_message(data["account_sid"], data["sid"])  # type: ignore[return-value]

    def update_message_status(
        self,
        sid: str,
        status: str,
        *,
        date_sent: str | None = None,
        error_code: int | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_rfc2822()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM messages WHERE sid = ?", (sid,)
            ).fetchone()
            if existing is None:
                return None
            merged_meta = json.loads(existing["metadata_json"] or "{}")
            if metadata:
                merged_meta.update(metadata)
            self._conn.execute(
                """
                UPDATE messages
                SET status = ?, date_updated = ?, date_sent = COALESCE(?, date_sent),
                    error_code = ?, error_message = ?, metadata_json = ?
                WHERE sid = ?
                """,
                (
                    status,
                    now,
                    date_sent,
                    error_code,
                    error_message,
                    json.dumps(merged_meta),
                    sid,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM messages WHERE sid = ?", (sid,)
            ).fetchone()
            return dict(row) if row else None

    def get_message(self, account_sid: str, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE account_sid = ? AND sid = ?",
                (account_sid, sid),
            ).fetchone()
            return dict(row) if row else None

    def list_messages(
        self,
        account_sid: str,
        *,
        to_number: str | None = None,
        from_number: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["account_sid = ?"]
        params: list[Any] = [account_sid]
        if to_number:
            clauses.append("to_number = ?")
            params.append(to_number)
        if from_number:
            clauses.append("from_number = ?")
            params.append(from_number)
        params.extend([limit, offset])
        sql = (
            f"SELECT * FROM messages WHERE {' AND '.join(clauses)} "
            "ORDER BY rowid DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def delete_message(self, account_sid: str, sid: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE account_sid = ? AND sid = ?",
                (account_sid, sid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # --- Calls ---

    def create_call(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO calls (
                    sid, account_sid, to_number, from_number, url, twiml, method, status,
                    direction, adapter, duration, status_callback, status_callback_event,
                    uri, date_created, date_updated, start_time, end_time, twiml_log_json,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["sid"],
                    data["account_sid"],
                    data["to_number"],
                    data["from_number"],
                    data.get("url"),
                    data.get("twiml"),
                    data.get("method", "POST"),
                    data["status"],
                    data["direction"],
                    data["adapter"],
                    data.get("duration"),
                    data.get("status_callback"),
                    data.get("status_callback_event"),
                    data["uri"],
                    data["date_created"],
                    data["date_updated"],
                    data.get("start_time"),
                    data.get("end_time"),
                    json.dumps(data.get("twiml_log", [])),
                    json.dumps(data.get("metadata", {})),
                ),
            )
            self._conn.commit()
            return self.get_call(data["account_sid"], data["sid"])  # type: ignore[return-value]

    def update_call(
        self,
        sid: str,
        *,
        status: str | None = None,
        duration: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        twiml_log: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_rfc2822()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM calls WHERE sid = ?", (sid,)
            ).fetchone()
            if existing is None:
                return None
            merged_meta = json.loads(existing["metadata_json"] or "{}")
            if metadata:
                merged_meta.update(metadata)
            new_log = (
                json.dumps(twiml_log)
                if twiml_log is not None
                else existing["twiml_log_json"]
            )
            self._conn.execute(
                """
                UPDATE calls
                SET status = COALESCE(?, status),
                    duration = COALESCE(?, duration),
                    start_time = COALESCE(?, start_time),
                    end_time = COALESCE(?, end_time),
                    twiml_log_json = ?,
                    metadata_json = ?,
                    date_updated = ?
                WHERE sid = ?
                """,
                (
                    status,
                    duration,
                    start_time,
                    end_time,
                    new_log,
                    json.dumps(merged_meta),
                    now,
                    sid,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM calls WHERE sid = ?", (sid,)
            ).fetchone()
            return dict(row) if row else None

    def get_call(self, account_sid: str, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calls WHERE account_sid = ? AND sid = ?",
                (account_sid, sid),
            ).fetchone()
            return dict(row) if row else None

    def list_calls(
        self,
        account_sid: str,
        *,
        to_number: str | None = None,
        from_number: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["account_sid = ?"]
        params: list[Any] = [account_sid]
        if to_number:
            clauses.append("to_number = ?")
            params.append(to_number)
        if from_number:
            clauses.append("from_number = ?")
            params.append(from_number)
        if status:
            clauses.append("status = ?")
            params.append(status)
        params.extend([limit, offset])
        sql = (
            f"SELECT * FROM calls WHERE {' AND '.join(clauses)} "
            "ORDER BY rowid DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # --- Recordings ---

    def create_recording(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO recordings (
                    sid, account_sid, call_sid, duration, status, channels, source,
                    uri, note, date_created, date_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["sid"],
                    data["account_sid"],
                    data["call_sid"],
                    data.get("duration", "0"),
                    data.get("status", "completed"),
                    data.get("channels", 1),
                    data.get("source", "RecordVerb"),
                    data["uri"],
                    data.get("note"),
                    data["date_created"],
                    data["date_updated"],
                ),
            )
            self._conn.commit()
            return self.get_recording(data["account_sid"], data["sid"])  # type: ignore[return-value]

    def get_recording(self, account_sid: str, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recordings WHERE account_sid = ? AND sid = ?",
                (account_sid, sid),
            ).fetchone()
            return dict(row) if row else None

    def list_call_recordings(self, account_sid: str, call_sid: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recordings WHERE account_sid = ? AND call_sid = ? ORDER BY rowid DESC",
                (account_sid, call_sid),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Webhooks ---

    def log_webhook_delivery(
        self,
        *,
        account_sid: str,
        resource_sid: str,
        event: str,
        target_url: str,
        method: str,
        request_body: dict[str, Any],
        signature: str,
        status_code: int | None,
        response_snippet: str | None,
        error: str | None,
    ) -> int:
        now = utc_now_iso()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO webhook_deliveries (
                    account_sid, resource_sid, event, target_url, method,
                    request_body_json, signature, status_code, response_snippet, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_sid,
                    resource_sid,
                    event,
                    target_url,
                    method,
                    json.dumps(request_body),
                    signature,
                    status_code,
                    response_snippet,
                    error,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_webhook_deliveries(
        self, account_sid: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            if account_sid:
                rows = self._conn.execute(
                    "SELECT * FROM webhook_deliveries WHERE account_sid = ? ORDER BY id DESC LIMIT ?",
                    (account_sid, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM webhook_deliveries ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def stats(self, account_sid: str) -> dict[str, int]:
        with self._lock:
            msg_count = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE account_sid = ?", (account_sid,)
            ).fetchone()[0]
            call_count = self._conn.execute(
                "SELECT COUNT(*) FROM calls WHERE account_sid = ?", (account_sid,)
            ).fetchone()[0]
            rec_count = self._conn.execute(
                "SELECT COUNT(*) FROM recordings WHERE account_sid = ?", (account_sid,)
            ).fetchone()[0]
            wh_count = self._conn.execute(
                "SELECT COUNT(*) FROM webhook_deliveries WHERE account_sid = ?",
                (account_sid,),
            ).fetchone()[0]
            return {
                "messages": int(msg_count),
                "calls": int(call_count),
                "recordings": int(rec_count),
                "webhooks": int(wh_count),
            }
