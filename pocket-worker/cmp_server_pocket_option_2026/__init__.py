"""cmp_server_pocket_option_2026 — a modern async client for the PocketOption WebSocket API.

Work in progress. Ships the transport, the high-level client, and typed models;
streams/candles and the MCP server are added incrementally.
"""
from __future__ import annotations

from ._transport import Transport, parse_ssid
from .client import AsyncPocketOption
from .constants import Region
from .exceptions import (
    AssetError,
    AuthenticationError,
    ConnectionFailedError,
    LiveTradingDisabled,
    NotAuthorizedError,
    OrderError,
    OrderTimeout,
    PocketOptionError,
)
from .models import Asset, Balance, Candle, Deal, Direction

try:  # single source of truth is the installed distribution metadata
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("cmp-server-pocket-option-2026")
    except PackageNotFoundError:  # running from a source checkout, not installed
        __version__ = "0.1.0"
except ImportError:  # pragma: no cover - Python <3.8 has no importlib.metadata
    __version__ = "0.1.0"

__all__ = (
    "AsyncPocketOption",
    "Transport",
    "parse_ssid",
    "Region",
    "Asset",
    "Balance",
    "Candle",
    "Deal",
    "Direction",
    "PocketOptionError",
    "ConnectionFailedError",
    "AuthenticationError",
    "NotAuthorizedError",
    "AssetError",
    "OrderError",
    "OrderTimeout",
    "LiveTradingDisabled",
    "__version__",
)
