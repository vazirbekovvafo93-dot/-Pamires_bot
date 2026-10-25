"""Exception hierarchy for cmp_server_pocket_option_2026.

Everything the library raises derives from `PocketOptionError`, so callers can
catch the whole family with a single `except`.
"""
from __future__ import annotations

__all__ = (
    "PocketOptionError",
    "ConnectionFailedError",
    "AuthenticationError",
    "NotAuthorizedError",
    "AssetError",
    "OrderError",
    "OrderTimeout",
    "LiveTradingDisabled",
)


class PocketOptionError(Exception):
    """Base class for all library errors."""


class ConnectionFailedError(PocketOptionError):
    """Could not establish a WebSocket connection to any region."""


class AuthenticationError(PocketOptionError):
    """The auth handshake failed (bad or expired SSID, malformed payload)."""


class NotAuthorizedError(AuthenticationError):
    """The server explicitly rejected the session (`NotAuthorized`)."""


class AssetError(PocketOptionError):
    """The asset is unknown, closed, or the trade parameters are invalid."""


class OrderError(PocketOptionError):
    """An order could not be placed or was rejected by the server."""


class OrderTimeout(OrderError):
    """No response was received for an order within the timeout."""


class LiveTradingDisabled(PocketOptionError):
    """A real-money order was attempted while allow_real=False (the default)."""
