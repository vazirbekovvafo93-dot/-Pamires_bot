"""Read-only Pocket Option market-data service for SIGNAL AI.

Required environment:
  POCKET_OPTION_SSID: full Socket.IO auth payload, kept secret.
  MARKET_API_TOKEN: long random token shared with the Node.js API.

This service intentionally does not expose or call order/trading methods.
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pocketoptionapi import PocketOption

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("signal-pocket-worker")

app = FastAPI(title="SIGNAL AI Pocket Option Market Data", version="0.1.0")
api: PocketOption | None = None
connection_state = "not_configured"
connection_error: str | None = None
connection_error_kind: str | None = None
data_lock = threading.Lock()

ALLOWED_PERIODS = {5, 15, 60, 300, 900, 3600}


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("MARKET_API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Market worker token is not configured")
    supplied = authorization or ""
    if not hmac.compare_digest(supplied, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def public_asset(symbol: str, info: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": info.get("name") or symbol,
        "is_available": bool(info.get("is_available")),
        "timeframes": info.get("timeframes") or [],
        "payout": info.get("payout"),
    }


@app.on_event("startup")
def startup() -> None:
    global api, connection_state, connection_error, connection_error_kind
    ssid = os.getenv("POCKET_OPTION_SSID", "").strip()
    if not ssid:
        connection_state = "ssid_required"
        logger.warning("POCKET_OPTION_SSID is not set; worker started in setup mode")
        return
    if not os.getenv("MARKET_API_TOKEN"):
        connection_state = "token_required"
        logger.error("MARKET_API_TOKEN is not set")
        return

    try:
        candidate = PocketOption(ssid)
        ok, error = candidate.connect()
        if not ok:
            connection_state = "connection_failed"
            connection_error = "The provider client rejected the connection request."
            connection_error_kind = type(error).__name__ if error is not None else "ConnectionRejected"
            logger.error("Pocket Option connection failed; error_type=%s", connection_error_kind)
            return

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if candidate.check_connect() and candidate.is_time_synced():
                api = candidate
                connection_state = "connected"
                connection_error = None
                connection_error_kind = None
                logger.info("Pocket Option WebSocket connected and time synchronized")
                return
            time.sleep(0.25)

        candidate.disconnect_websocket()
        connection_state = "time_sync_timeout"
        connection_error = "Server time did not synchronize within 30 seconds."
        connection_error_kind = "TimeSyncTimeout"
        logger.error(connection_error)
    except Exception as exc:
        connection_state = "connection_failed"
        connection_error = "Provider initialization raised an exception."
        connection_error_kind = type(exc).__name__
        logger.exception("Pocket Option initialization failed; error_type=%s", connection_error_kind)


@app.on_event("shutdown")
def shutdown() -> None:
    global api
    if api is not None:
        try:
            api.disconnect_websocket()
        except Exception:
            logger.exception("Error closing Pocket Option WebSocket")
        api = None


def _runtime_connection_state() -> str:
    """Report current socket readiness instead of trusting startup state alone."""
    global connection_state, connection_error, connection_error_kind
    if api is None:
        return connection_state
    try:
        if not api.check_connect():
            connection_state = "disconnected"
            connection_error = "The provider socket is not connected."
            connection_error_kind = "SocketDisconnected"
        elif not api.is_time_synced():
            connection_state = "time_sync_pending"
            connection_error = "Provider server time is not synchronized."
            connection_error_kind = "TimeSyncPending"
        else:
            connection_state = "connected"
            connection_error = None
            connection_error_kind = None
    except Exception as exc:
        connection_state = "connection_check_failed"
        connection_error = "Runtime connection check raised an exception."
        connection_error_kind = type(exc).__name__
        logger.warning("Runtime connection check failed; error_type=%s", connection_error_kind)
    return connection_state


@app.get("/health")
def health() -> dict[str, Any]:
    state = _runtime_connection_state()
    return {
        "service": "signal-ai-pocket-worker",
        "status": "ok",
        "connection": state,
        "market_data_ready": state == "connected",
    }


@app.get("/health/details", dependencies=[Depends(require_token)])
def health_details() -> dict[str, Any]:
    """Authenticated diagnostics; never returns credentials or provider exception text."""
    state = _runtime_connection_state()
    return {
        "service": "signal-ai-pocket-worker",
        "connection": state,
        "market_data_ready": state == "connected",
        "ssid_configured": bool(os.getenv("POCKET_OPTION_SSID", "").strip()),
        "market_api_token_configured": bool(os.getenv("MARKET_API_TOKEN", "")),
        "error": connection_error,
        "error_type": connection_error_kind,
    }


@app.get("/api/assets", dependencies=[Depends(require_token)])
def assets(otc_only: bool = True) -> dict[str, Any]:
    if api is None or connection_state != "connected":
        raise HTTPException(status_code=503, detail=f"Market connection is {connection_state}")
    with data_lock:
        catalog = api.get_assets() or {}
        items = [
            public_asset(symbol, info)
            for symbol, info in catalog.items()
            if not otc_only or "otc" in symbol.lower() or "otc" in str(info.get("name", "")).lower()
        ]
    return {"count": len(items), "assets": items}


@app.get("/api/candles", dependencies=[Depends(require_token)])
def candles(
    symbol: str = Query(min_length=2, max_length=80),
    period: int = Query(default=60),
    count: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if period not in ALLOWED_PERIODS:
        raise HTTPException(status_code=400, detail=f"Unsupported period; allowed: {sorted(ALLOWED_PERIODS)}")
    if api is None or connection_state != "connected":
        raise HTTPException(status_code=503, detail=f"Market connection is {connection_state}")

    with data_lock:
        catalog = api.get_assets() or {}
        info = catalog.get(symbol)
        if not info:
            raise HTTPException(status_code=404, detail="Unknown asset symbol")
        if not info.get("is_available"):
            raise HTTPException(status_code=409, detail="Asset is not currently available")
        if not api.subscribe(symbol, period=period):
            raise HTTPException(status_code=502, detail="Could not subscribe to market stream")
        rows = api.get_historical_candles(
            symbol,
            period=period,
            offset=count,
            count_request=1,
        )
        if not rows:
            raise HTTPException(status_code=502, detail="No candle data returned by Pocket Option")

    normalized = []
    for row in rows[-count:]:
        try:
            normalized.append({
                "time": int(row.get("time", row.get("timestamp"))),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
            })
        except (TypeError, ValueError, KeyError):
            continue

    if not normalized:
        raise HTTPException(status_code=502, detail="Candle payload was not in the expected OHLC format")
    return {
        "symbol": symbol,
        "period": period,
        "count": len(normalized),
        "candles": normalized,
        "source": "pocket-option-unofficial-websocket",
    }
