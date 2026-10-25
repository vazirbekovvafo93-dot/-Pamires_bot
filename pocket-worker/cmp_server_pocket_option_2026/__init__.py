"""Read-only Pocket Option market-data client."""
from ._transport import Transport, parse_ssid
from .client import AsyncPocketOption
from .constants import Region
from .exceptions import AuthenticationError, ConnectionFailedError, NotAuthorizedError, PocketOptionError
from .models import Asset, Candle

__all__ = (
    "AsyncPocketOption",
    "Transport",
    "parse_ssid",
    "Region",
    "Asset",
    "Candle",
    "PocketOptionError",
    "ConnectionFailedError",
    "AuthenticationError",
    "NotAuthorizedError",
)
