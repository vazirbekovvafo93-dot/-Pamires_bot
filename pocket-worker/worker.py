"""Read-only Pocket Option market-data worker.

Uses the read-only adapter ported from PMSignalPro:
  pocket_feed_cmp.py + pocket_option_cmp.py
SSID remains in Render environment only (PO_SSID or legacy POCKET_OPTION_SSID).
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
import pocket_feed_cmp as po_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("signal-pocket-worker")

app = FastAPI(title="SIGNAL AI Pocket Option Market Data", version="0.2.0")
data_lock = threading.Lock()
ALLOWED_PERIODS = {5, 15, 60, 300, 900, 3600}


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("MARKET_API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Market worker token is not configured")
    if not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def status_snapshot() -> dict[str, Any]:
    configured = po_feed.configured()
    api = po_feed.client() if configured else None
    connected = bool(api and api.check_connect())
    return {
        "service": "signal-ai-pocket-worker",
        "status": "ok",
        "connection": "connected" if connected else ("ssid_required" if not configured else "connection_failed"),
    }


@app.on_event("startup")
def startup() -> None:
    if not (os.getenv("PO_SSID") or os.getenv("POCKET_OPTION_SSID") or "").strip():
        logger.warning("Pocket Option SSID is not set; worker started in setup mode")
        return
    if not os.getenv("MARKET_API_TOKEN"):
        logger.error("MARKET_API_TOKEN is not set")


@app.on_event("shutdown")
def shutdown() -> None:
    try:
        api = po_feed.client()
        if api is not None:
            api.disconnect_websocket()
    except Exception:
        logger.exception("Error closing Pocket Option WebSocket")


@app.get("/health")
def health() -> dict[str, Any]:
    # Do not attempt an expensive authentication on every health probe.
    configured = po_feed.configured()
    if not configured:
        state = "ssid_required"
    else:
        try:
            api = po_feed.client()
            state = "connected" if api and api.check_connect() else "connection_failed"
        except Exception:
            state = "connection_failed"
    return {"service": "signal-ai-pocket-worker", "status": "ok", "connection": state}


def get_catalog() -> dict[str, Any]:
    api = po_feed.client()
    if api is None or not api.check_connect():
        raise HTTPException(status_code=503, detail="Pocket Option connection is not ready")
    catalog = po_feed.get_assets(force=True)
    if not isinstance(catalog, dict) or not catalog:
        raise HTTPException(status_code=502, detail="Pocket Option returned no asset catalog")
    return catalog


def public_asset(symbol: str, info: Any) -> dict[str, Any]:
    meta = info if isinstance(info, dict) else {}
    name = str(meta.get("name") or symbol)
    return {
        "symbol": symbol,
        "name": name,
        "is_available": bool(meta.get("is_active", meta.get("active", True))),
        "timeframes": sorted(ALLOWED_PERIODS),
        "payout": meta.get("payout", meta.get("profit")),
    }


@app.get("/api/assets", dependencies=[Depends(require_token)])
def assets(otc_only: bool = True) -> dict[str, Any]:
    with data_lock:
        catalog = get_catalog()
        items = [
            public_asset(symbol, info)
            for symbol, info in catalog.items()
            if not otc_only or "otc" in str(symbol).lower() or "otc" in str((info or {}).get("name", "") if isinstance(info, dict) else "").lower()
        ]
    return {"count": len(items), "assets": items, "source": "pocket-option-unofficial-websocket"}


@app.get("/api/candles", dependencies=[Depends(require_token)])
def candles(
    symbol: str = Query(min_length=2, max_length=80),
    period: int = Query(default=60),
    count: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if period not in ALLOWED_PERIODS:
        raise HTTPException(status_code=400, detail=f"Unsupported period; allowed: {sorted(ALLOWED_PERIODS)}")
    with data_lock:
        catalog = get_catalog()
        if symbol not in catalog:
            raise HTTPException(status_code=404, detail="Unknown asset symbol")
        meta = catalog[symbol]
        if isinstance(meta, dict) and not meta.get("is_active", meta.get("active", True)):
            raise HTTPException(status_code=409, detail="Asset is not currently available")
        try:
            rows, resolved = po_feed.candles(symbol, timeframe=_timeframe(period), count=count)
        except Exception as exc:
            logger.warning("Pocket Option candles unavailable for requested symbol (details omitted)")
            raise HTTPException(status_code=502, detail="Pocket Option candle feed is unavailable for this symbol/timeframe") from exc

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
        raise HTTPException(status_code=502, detail="Pocket Option returned candles in an unsupported format")
    return {
        "symbol": resolved,
        "period": period,
        "count": len(normalized),
        "candles": normalized,
        "source": "pocket-option-unofficial-websocket",
    }


def _timeframe(period: int) -> str:
    return {5: "S5", 15: "S15", 60: "M1", 300: "M5", 900: "M15", 3600: "H1"}[period]
