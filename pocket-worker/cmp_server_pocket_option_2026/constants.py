"""Static configuration for the PocketOption transport.

Values here were verified against the current browser client. The regions are
bare WebSocket hosts; `python-socketio` appends `/socket.io/?EIO=4&transport=websocket`.
"""
from __future__ import annotations

import enum

__all__ = (
    "DEFAULT_ORIGIN",
    "DEFAULT_USER_AGENT",
    "PING_INTERVAL",
    "MIN_ORDER_AMOUNT",
    "MAX_ORDER_AMOUNT",
    "MIN_DURATION",
    "MAX_DURATION",
    "MAX_CONCURRENT_ORDERS",
    "Region",
    "LIVE_REGIONS",
    "DEMO_REGIONS",
)

# Fingerprint defaults observed from the current web client.
# NOTE: these are sane *current* defaults, not fingerprint spoofing. A static UA
# does not make the client look like a browser — see the connection notes.
DEFAULT_ORIGIN = "https://m.pocketoption.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
)

# App-level "ps" keep-alive interval (seconds). Engine.IO's own 2/3 heartbeat
# is handled by socketio; this is the extra ping PocketOption expects.
PING_INTERVAL = 20

# Client-side sanity limits, enforced before a request reaches the server.
MIN_ORDER_AMOUNT = 1
MAX_ORDER_AMOUNT = 50_000
MIN_DURATION = 5
MAX_DURATION = 43_200
MAX_CONCURRENT_ORDERS = 10


class Region(str, enum.Enum):
    """Known PocketOption WebSocket endpoints (bare host)."""

    # --- live ---
    EUROPE = "wss://api-eu.po.market"
    US_NORTH = "wss://api-us-north.po.market"
    US_SOUTH = "wss://api-us-south.po.market"
    ASIA = "wss://api-asia.po.market"
    US2 = "wss://api-us2.po.market"
    US3 = "wss://api-us3.po.market"
    US4 = "wss://api-us4.po.market"
    FRANCE = "wss://api-fr.po.market"
    FRANCE2 = "wss://api-fr2.po.market"
    RUSSIA = "wss://api-msk.po.market"
    INDIA = "wss://api-in.po.market"
    FINLAND = "wss://api-fin.po.market"
    SEYCHELLES = "wss://api-sc.po.market"
    HONGKONG = "wss://api-hk.po.market"
    SERVER_SPB = "wss://api-spb.po.market"
    SERVER_L = "wss://api-l.po.market"
    SERVER_C = "wss://api-c.po.market"

    # --- demo ---
    DEMO = "wss://demo-api-eu.po.market"
    DEMO_2 = "wss://try-demo-eu.po.market"


# Ordered subsets used for failover, best-first.
LIVE_REGIONS = [
    Region.EUROPE,
    Region.US_NORTH,
    Region.US_SOUTH,
    Region.ASIA,
    Region.US2,
    Region.US3,
    Region.US4,
    Region.SERVER_L,
    Region.SERVER_C,
    Region.SERVER_SPB,
]

DEMO_REGIONS = [Region.DEMO, Region.DEMO_2]
