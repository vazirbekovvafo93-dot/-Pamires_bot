"""Read-only Pocket Option market-data client."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from ._transport import Transport
from .constants import Region
from .models import Asset, Candle

logger = logging.getLogger("cmp_server_pocket_option_2026.client")


class AsyncPocketOption:
    """High-level PocketOption client."""

    def __init__(
        self,
        ssid: str,
        *,
        demo: Optional[bool] = None,
        allow_real: bool = False,
        **transport_kwargs: Any,
    ) -> None:
        self._t = Transport(ssid, demo=demo, **transport_kwargs)
        self._t.set_dispatcher(self._dispatch)

        # state
        self._assets: Dict[str, Asset] = {}

        # (asset, timeframe) -> future for a pending candle request
        self._pending_history: Dict[tuple, "asyncio.Future[list]"] = {}

        # event -> handler
        self._handlers = {
            "updateAssets": self._on_assets,
            "loadHistoryPeriod": self._on_history,
            "updateHistoryNewFast": self._on_history,
            "updateStream": self._on_stream,
        }

    # ---------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "AsyncPocketOption":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def connect(self, *, ready_timeout: float = 8.0) -> Region:
        """Connect, authenticate, and wait for the initial account state."""
        region = await self._t.connect()
        # balance + assets arrive on their own right after auth; give them a moment
        return region

    async def close(self) -> None:
        for fut in self._pending_history.values():
            if not fut.done():
                fut.cancel()
        await self._t.disconnect()

    @property
    def connected(self) -> bool:
        return self._t.connected

    # ------------------------------------------------------------------- state

    def assets(self, *, active_only: bool = False) -> List[Asset]:
        """All known assets. Set ``active_only`` to drop closed ones."""
        values = list(self._assets.values())
        return [a for a in values if a.is_active] if active_only else values

    def asset(self, symbol: str) -> Optional[Asset]:
        """Look up a single asset by symbol (e.g. ``"EURUSD_otc"``)."""
        return self._assets.get(symbol)

    # -------------------------------------------------------------- market data

    async def candles(
        self,
        asset: str,
        timeframe: int,
        *,
        count: Optional[int] = None,
        timeout: float = 10.0,
    ) -> List[Candle]:
        """Fetch OHLCV candles for ``asset`` at ``timeframe`` seconds.

        ``timeframe`` is the candle size in seconds (60=M1, 300=M5, 3600=H1,
        14400=H4, ...). Returns candles oldest-first. ``count`` trims to the most
        recent N. You can call this for several timeframes concurrently.
        """
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[list]" = loop.create_future()
        key = (asset, timeframe)
        self._pending_history[key] = fut

        await self._t.emit("changeSymbol", {"asset": asset, "period": timeframe})
        try:
            rows = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending_history.pop(key, None)
            raise OrderTimeout(
                f"No candle history for {asset}@{timeframe}s within {timeout}s"
            ) from None

        candles = sorted((Candle.from_row(r) for r in rows), key=lambda c: c.time)
        if count is not None:
            candles = candles[-count:]
        return candles

    async def _dispatch(self, event: str, data: Any) -> None:
        handler = self._handlers.get(event)
        if handler is None:
            return  # unhandled events are fine; we add handlers as we model them
        try:
            await handler(data)
        except Exception:
            logger.exception("Handler error on event %r", event)

    async def _on_assets(self, data: Any) -> None:
        if not isinstance(data, list):
            return
        parsed: Dict[str, Asset] = {}
        for row in data:
            try:
                asset = Asset.from_row(row)
                if asset.symbol:
                    parsed[asset.symbol] = asset
            except Exception:
                logger.debug("Could not parse asset row: %r", row, exc_info=True)
        if parsed:
            self._assets = parsed
    
    async def _on_history(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        key = (data.get("asset"), data.get("period"))
        candles = data.get("candles", [])
        fut = self._pending_history.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(candles)
        else:
            logger.debug("history for unrequested %s (%d candles)", key, len(candles))

