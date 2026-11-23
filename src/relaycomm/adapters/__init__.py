"""Pluggable carrier adapters for Relaycomm."""

from relaycomm.adapters.base import AdapterCallResult, AdapterSmsResult, CarrierAdapter
from relaycomm.adapters.registry import get_adapter, list_adapters

__all__ = [
    "AdapterCallResult",
    "AdapterSmsResult",
    "CarrierAdapter",
    "get_adapter",
    "list_adapters",
]
