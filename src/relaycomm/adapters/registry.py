"""Registry for pluggable carrier adapters."""

from __future__ import annotations

from relaycomm.adapters.base import CarrierAdapter
from relaycomm.adapters.passthrough import (
    TelnyxPassthroughAdapter,
    VonagePassthroughAdapter,
)
from relaycomm.adapters.simulator import SimulatorAdapter
from relaycomm.adapters.sip_stub import SipStubAdapter
from relaycomm.adapters.smtp_gateway import SmtpGatewayAdapter
from relaycomm.config import Settings
from relaycomm.twiml import TwimlInterpreter


def build_adapters(
    settings: Settings, interpreter: TwimlInterpreter
) -> dict[str, CarrierAdapter]:
    return {
        "simulator": SimulatorAdapter(interpreter),
        "smtp_gateway": SmtpGatewayAdapter(
            host=settings.smtp_host,
            port=settings.smtp_port,
            from_addr=settings.smtp_from,
            default_carrier_domain=settings.smtp_default_carrier_domain,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
        ),
        "sip_stub": SipStubAdapter(
            engine=settings.sip_engine,
            endpoint=settings.sip_endpoint,
            trunk=settings.sip_trunk,
        ),
        "telnyx": TelnyxPassthroughAdapter(api_key=settings.telnyx_api_key),
        "vonage": VonagePassthroughAdapter(
            api_key=settings.vonage_api_key,
            api_secret=settings.vonage_api_secret,
        ),
    }


def get_adapter(
    adapters: dict[str, CarrierAdapter], name: str | None, default_name: str
) -> CarrierAdapter:
    chosen = (name or default_name or "simulator").strip().lower()
    if chosen not in adapters:
        raise ValueError(
            f"Unknown carrier adapter '{chosen}'. Available: {', '.join(sorted(adapters))}"
        )
    return adapters[chosen]


def list_adapters(adapters: dict[str, CarrierAdapter]) -> list[dict[str, object]]:
    return [adapter.info() for adapter in adapters.values()]
