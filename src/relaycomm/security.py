"""Authentication, SID generation, and Twilio-compatible HMAC-SHA1 webhook signatures."""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from typing import Mapping


def generate_sid(prefix: str) -> str:
    """Generate a 34-character Twilio-style SID (2-char prefix + 32 hex chars)."""
    if len(prefix) != 2 or not prefix.isalpha():
        raise ValueError("SID prefix must be 2 alphabetic characters")
    return f"{prefix.upper()}{uuid.uuid4().hex}"


def compute_twilio_signature(
    auth_token: str, url: str, params: Mapping[str, str] | None = None
) -> str:
    """Compute an X-Twilio-Signature compatible HMAC-SHA1 signature.

    Algorithm:
    1. Start with the full request URL (including query string if any).
    2. Sort POST form parameters alphabetically by key (ASCII/UTF-8 codepoint order).
    3. Append each key followed immediately by its value to the URL string.
    4. Compute HMAC-SHA1 using ``auth_token`` as key and base64-encode the digest.
    """
    payload = url
    if params:
        for key in sorted(params.keys()):
            val = params[key]
            payload += f"{key}{'' if val is None else str(val)}"
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: Mapping[str, str] | None,
    signature: str,
) -> bool:
    """Verify an X-Twilio-Signature header using constant-time comparison."""
    expected = compute_twilio_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature or "")


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
