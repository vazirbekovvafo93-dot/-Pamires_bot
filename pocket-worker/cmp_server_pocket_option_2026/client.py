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
        **transport_kwargs: Any,
    ) -> None:
        self._t = Transport(ssid, demo=demo, **transport_kwargs)
        self._t.set_dispatcher(self._dispatch)

        # state
        self._assets: Dict[str, Asset] = {}

        self._assets_ready = asyncio.Event()

        # (asset, timeframe) -> future for a pending candle request
        self._pending_history: Dict[tuple, "asyncio.Future[list]"] = {}

        # Read-only tick cache; supports short timeframe candle aggregation.
        self._last_price: Dict[str, float] = {}
        self._tick_callbacks: List[Any] = []

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
        try:
            await asyncio.wait_for(self._assets_ready.wait(), ready_timeout)
        except asyncio.TimeoutError:
            logger.warning("Pocket Option connected but asset catalog did not arrive before timeout")
        return region

    async def close(self) -> None:
        for fut in self._pending_history.values():
            if not fut.done():
                fut.cancel()
        await self._t.disconnect()

    @property
    def is_demo(self) -> bool:
        return self._t.is_demo

    @property
    def connected(self) -> bool:
        return self._t.connected

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
            raise TimeoutError(f"No candle history for {asset}@{timeframe}s within {timeout}s") from None

        candles = sorted((Candle.from_row(r) for r in rows), key=lambda c: c.time)
        if count is not None:
            candles = candles[-count:]
        return candles

    async def subscribe(self, asset: str) -> None:
        """Subscribe to the live tick stream for an asset."""
        await self._t.emit("subscribeSymbol", asset)

    def on_tick(self, callback: Any) -> None:
        """Register ``callback(asset, price, timestamp)`` for each live tick."""
        self._tick_callbacks.append(callback)

    def price(self, asset: str) -> Optional[float]:
        """Last live price seen for ``asset`` (None if not subscribed yet)."""
        return self._last_price.get(asset)

    # -------------------------------------------------------------- event routing

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
            self._assets_ready.set()

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

    async def _on_stream(self, data: Any) -> None:
        # updateStream carries live ticks. Its sibling `history` uses [ts, price];
        # some builds prefix the asset as [asset, ts, price]. Handle both shapes.
        ticks = data if isinstance(data, list) else [data]
        for tick in ticks:
            if not isinstance(tick, list):
                continue
            asset: Optional[str] = None
            timestamp: Optional[float] = None
            price: Optional[float] = None
            if len(tick) >= 3 and isinstance(tick[0], str):
                asset, timestamp, price = tick[0], tick[1], tick[2]
            elif len(tick) >= 2:
                timestamp, price = tick[0], tick[1]
            if price is None:
                continue
            if asset is not None:
                self._last_price[asset] = price
            for cb in self._tick_callbacks:
                try:
                    res = cb(asset, price, timestamp)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    logger.exception("on_tick callback error")

