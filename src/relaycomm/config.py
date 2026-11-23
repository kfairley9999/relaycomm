"""Configuration settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: str
    account_sid: str
    auth_token: str
    adapter: str
    base_url: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool
    smtp_default_carrier_domain: str
    sip_engine: str
    sip_endpoint: str
    sip_trunk: str
    telnyx_api_key: str
    vonage_api_key: str
    vonage_api_secret: str
    webhook_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("RELAYCOMM_HOST", "0.0.0.0"),
            port=int(os.getenv("RELAYCOMM_PORT", "8080")),
            db_path=os.getenv("RELAYCOMM_DB_PATH", "./relaycomm.db"),
            account_sid=os.getenv(
                "RELAYCOMM_ACCOUNT_SID", "AC00000000000000000000000000000001"
            ),
            auth_token=os.getenv("RELAYCOMM_AUTH_TOKEN", "dev_auth_token_change_me"),
            adapter=os.getenv("RELAYCOMM_ADAPTER", "simulator").strip().lower(),
            base_url=os.getenv("RELAYCOMM_BASE_URL", "http://localhost:8080").rstrip("/"),
            smtp_host=os.getenv("RELAYCOMM_SMTP_HOST", "localhost"),
            smtp_port=int(os.getenv("RELAYCOMM_SMTP_PORT", "1025")),
            smtp_user=os.getenv("RELAYCOMM_SMTP_USER", ""),
            smtp_password=os.getenv("RELAYCOMM_SMTP_PASSWORD", ""),
            smtp_from=os.getenv("RELAYCOMM_SMTP_FROM", "relaycomm@example.local"),
            smtp_use_tls=_env_bool("RELAYCOMM_SMTP_USE_TLS", False),
            smtp_default_carrier_domain=os.getenv(
                "RELAYCOMM_SMTP_DEFAULT_CARRIER_DOMAIN", "vtext.com"
            ),
            sip_engine=os.getenv("RELAYCOMM_SIP_ENGINE", "asterisk_ari"),
            sip_endpoint=os.getenv("RELAYCOMM_SIP_ENDPOINT", "http://localhost:8088/ari"),
            sip_trunk=os.getenv("RELAYCOMM_SIP_TRUNK", "trunk-primary"),
            telnyx_api_key=os.getenv("RELAYCOMM_TELNYX_API_KEY", ""),
            vonage_api_key=os.getenv("RELAYCOMM_VONAGE_API_KEY", ""),
            vonage_api_secret=os.getenv("RELAYCOMM_VONAGE_API_SECRET", ""),
            webhook_timeout_seconds=float(
                os.getenv("RELAYCOMM_WEBHOOK_TIMEOUT_SECONDS", "5.0")
            ),
        )
