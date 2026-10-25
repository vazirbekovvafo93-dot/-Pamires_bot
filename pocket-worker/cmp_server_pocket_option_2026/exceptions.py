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
)


class PocketOptionError(Exception):
    """Base class for all library errors."""


class ConnectionFailedError(PocketOptionError):
    """Could not establish a WebSocket connection to any region."""


class AuthenticationError(PocketOptionError):
    """The auth handshake failed (bad or expired SSID, malformed payload)."""


class NotAuthorizedError(AuthenticationError):
    """The server explicitly rejected the session (`NotAuthorized`)."""


