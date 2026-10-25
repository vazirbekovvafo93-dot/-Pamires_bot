"""
cmp_server_pocket_option_2026.client
=======================

The high-level, ergonomic client. It sits on top of :class:`Transport` and turns
the raw event stream into typed state and simple coroutines.

    async with AsyncPocketOption(ssid) as po:
        print(await po.get_balance())
        deal = await po.buy("EURUSD_otc", "call", amount=1, expiration=60)
        result = await po.result(deal)          # waits for it to close
        print(result.won, result.profit)

Design
------
* One event **router**: every inbound event is validated into a model and
  dispatched to a handler. No ``if "balance" in message`` string matching.
* Trades correlate by ``requestId`` using ``asyncio`` futures — a ``buy`` resolves
  when its own ``successopenOrder`` arrives, and ``result`` resolves on the
  matching ``successcloseOrder``. No polling, no shared globals.
* **Safety default:** real-money orders are refused unless ``allow_real=True``.
  Demo works out of the box.

Only the pieces we have verified against real traffic are implemented (balance,
assets, buy, result). Streams/candles are added next.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional

from ._transport import Transport
from .constants import (
    MAX_ORDER_AMOUNT,
    MIN_ORDER_AMOUNT,
    Region,
)
from .exceptions import (
    AssetError,
    LiveTradingDisabled,
    OrderError,
    OrderTimeout,
)
from .models import Asset, Balance, Candle, Deal, Direction

logger = logging.getLogger("cmp_server_pocket_option_2026.client")

_MAX_REQUEST_ID = 2_147_483_647  # int32 ceiling the server expects


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
        self._allow_real = allow_real

        # state
        self._balance: Optional[Balance] = None
        self._assets: Dict[str, Asset] = {}

        # readiness events (server pushes these shortly after auth)
        self._balance_ready = asyncio.Event()
        self._assets_ready = asyncio.Event()

        # requestId -> future
        self._pending_open: Dict[int, "asyncio.Future[Deal]"] = {}
        self._pending_close: Dict[int, "asyncio.Future[Deal]"] = {}

        # (asset, timeframe) -> future for a pending candle request
        self._pending_history: Dict[tuple, "asyncio.Future[list]"] = {}

        # live prices from updateStream, plus optional tick callbacks
        self._last_price: Dict[str, float] = {}
        self._tick_callbacks: List[Any] = []
        # per-asset waiters woken by the next tick (event-driven get_price, no polling)
        self._price_waiters: Dict[str, List["asyncio.Future[float]"]] = {}

        # positions & history (both are lists of Deal)
        self._open_deals: Dict[str, Deal] = {}      # snapshot of currently open
        self._closed_deals: Dict[str, Deal] = {}    # accumulated history by id

        # event -> handler
        self._handlers = {
            "successupdateBalance": self._on_balance,
            "updateAssets": self._on_assets,
            "successopenOrder": self._on_open_order,
            "successcloseOrder": self._on_close_order,
            "loadHistoryPeriod": self._on_history,
            "updateHistoryNewFast": self._on_history,
            "updateStream": self._on_stream,
            "updateOpenedDeals": self._on_opened_deals,
            "updateClosedDeals": self._on_closed_deals,
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
        try:
            await asyncio.wait_for(
                asyncio.gather(self._balance_ready.wait(), self._assets_ready.wait()),
                ready_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Connected, but initial balance/assets did not all arrive within %ss "
                "(balance=%s, assets=%d)",
                ready_timeout, self._balance is not None, len(self._assets),
            )
        return region

    async def close(self) -> None:
        pending: List["asyncio.Future"] = []
        pending += list(self._pending_open.values())
        pending += list(self._pending_close.values())
        pending += list(self._pending_history.values())
        for waiters in self._price_waiters.values():
            pending += waiters
        for fut in pending:
            if not fut.done():
                fut.cancel()
        self._price_waiters.clear()
        await self._t.disconnect()

    @property
    def is_demo(self) -> bool:
        return self._t.is_demo

    @property
    def connected(self) -> bool:
        return self._t.connected

    # ------------------------------------------------------------------- state

    async def get_balance(self, *, timeout: float = 5.0) -> Balance:
        """Return the current balance, waiting for it if not received yet."""
        if self._balance is None:
            await asyncio.wait_for(self._balance_ready.wait(), timeout)
        assert self._balance is not None
        return self._balance

    @property
    def balance(self) -> Optional[Balance]:
        """Last known balance without waiting (may be None early on)."""
        return self._balance

    def assets(self, *, active_only: bool = False) -> List[Asset]:
        """All known assets. Set ``active_only`` to drop closed ones."""
        values = list(self._assets.values())
        return [a for a in values if a.is_active] if active_only else values

    def asset(self, symbol: str) -> Optional[Asset]:
        """Look up a single asset by symbol (e.g. ``"EURUSD_otc"``)."""
        return self._assets.get(symbol)

    # ------------------------------------------------------------------ trading

    async def buy(
        self,
        asset: str,
        direction: "Direction | str | int",
        amount: float,
        expiration: int,
        *,
        timeout: float = 15.0,
    ) -> Deal:
        """Open a trade and return the confirmed (opened) :class:`Deal`.

        ``direction`` accepts 'call'/'up'/0 or 'put'/'down'/1.
        ``expiration`` is in **seconds** (any value the asset allows).

        Use :meth:`result` on the returned deal to wait for win/loss.
        """
        side = Direction.coerce(direction)

        # --- validate before sending -------------------------------------
        if not self._allow_real and not self.is_demo:
            raise LiveTradingDisabled(
                "Refusing a real-money order. Pass allow_real=True to enable "
                "live trading (demo works without it)."
            )

        if not (MIN_ORDER_AMOUNT <= amount <= MAX_ORDER_AMOUNT):
            raise AssetError(
                f"amount {amount} out of range [{MIN_ORDER_AMOUNT}, {MAX_ORDER_AMOUNT}]"
            )

        info = self._assets.get(asset)
        if info is not None:
            if not info.is_active:
                raise AssetError(f"asset {asset!r} is currently closed")
            if info.expirations and expiration not in info.expirations:
                raise AssetError(
                    f"expiration {expiration}s is not available for {asset} — "
                    f"allowed: {info.expirations}"
                )
        else:
            logger.warning("Asset %r not in the asset list; sending without validation", asset)

        # --- send + await confirmation -----------------------------------
        request_id = random.randint(1, _MAX_REQUEST_ID)
        loop = asyncio.get_running_loop()
        open_fut: "asyncio.Future[Deal]" = loop.create_future()
        close_fut: "asyncio.Future[Deal]" = loop.create_future()
        self._pending_open[request_id] = open_fut
        self._pending_close[request_id] = close_fut

        payload = {
            "asset": asset,
            "amount": amount,
            "action": side.value,        # "call" / "put"
            "isDemo": 1 if self.is_demo else 0,
            "requestId": request_id,
            "optionType": 100,
            "time": expiration,
        }
        logger.info("buy %s %s amount=%s exp=%ss (req=%s)", asset, side.value, amount, expiration, request_id)
        await self._t.emit("openOrder", payload)

        try:
            return await asyncio.wait_for(open_fut, timeout)
        except asyncio.TimeoutError:
            self._pending_open.pop(request_id, None)
            self._pending_close.pop(request_id, None)
            raise OrderTimeout(
                f"No 'successopenOrder' for {asset} within {timeout}s (req={request_id})"
            ) from None

    async def result(self, deal: Deal, *, timeout: Optional[float] = None) -> Deal:
        """Wait for ``deal`` to close and return the closed :class:`Deal`.

        Blocks until the trade expires. ``timeout`` defaults to the deal's own
        duration plus a small margin.
        """
        fut = self._pending_close.get(deal.request_id)
        if fut is None:
            raise OrderError(
                f"No pending result for req={deal.request_id}; was it opened by this client?"
            )
        if timeout is None:
            duration = max(0, deal.close_timestamp - deal.open_timestamp)
            timeout = duration + 15.0
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            self._pending_close.pop(deal.request_id, None)
            raise OrderTimeout(
                f"No 'successcloseOrder' for req={deal.request_id} within {timeout}s"
            ) from None

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

    async def subscribe(self, asset: str) -> None:
        """Subscribe to the live tick stream for ``asset`` (drives on_tick / price)."""
        await self._t.emit("subscribeSymbol", asset)

    def on_tick(self, callback: Any) -> None:
        """Register ``callback(asset, price, timestamp)`` for each live tick."""
        self._tick_callbacks.append(callback)

    def price(self, asset: str) -> Optional[float]:
        """Last live price seen for ``asset`` (None if not subscribed yet)."""
        return self._last_price.get(asset)

    async def wait_price(self, asset: str, *, timeout: float = 5.0) -> Optional[float]:
        """Subscribe if needed and return the next live price, event-driven.

        Resolves the instant a tick arrives (a future woken by ``_on_stream``) —
        no polling loop. Returns the last known price on timeout, or ``None`` if
        none was ever seen.
        """
        cached = self._last_price.get(asset)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[float]" = loop.create_future()
        self._price_waiters.setdefault(asset, []).append(fut)
        await self.subscribe(asset)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            waiters = self._price_waiters.get(asset)
            if waiters and fut in waiters:
                waiters.remove(fut)
            return self._last_price.get(asset)

    # --------------------------------------------------------- positions/history

    def open_deals(self) -> List[Deal]:
        """Trades currently open on the account (server snapshot)."""
        return list(self._open_deals.values())

    def history(self, *, limit: Optional[int] = None) -> List[Deal]:
        """Closed trades, most recent first. ``limit`` caps how many are returned."""
        deals = sorted(
            self._closed_deals.values(), key=lambda d: d.close_timestamp, reverse=True
        )
        return deals[:limit] if limit else deals

    def stats(self) -> Dict[str, Any]:
        """Quick performance summary over the closed trades we've seen.

        Useful for Claude to self-monitor (win rate, net P&L) rather than trading
        blind. Only covers trades observed this session (plus the initial history
        the server sends on connect).
        """
        deals = list(self._closed_deals.values())
        wins = [d for d in deals if d.profit > 0]
        losses = [d for d in deals if d.profit < 0]
        net = sum(d.profit for d in deals)
        total = len(deals)
        return {
            "trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / total) if total else 0.0,
            "net_profit": round(net, 2),
        }

    # ------------------------------------------------------------------ routing

    async def _dispatch(self, event: str, data: Any) -> None:
        handler = self._handlers.get(event)
        if handler is None:
            return  # unhandled events are fine; we add handlers as we model them
        try:
            await handler(data)
        except Exception:
            logger.exception("Handler error on event %r", event)

    async def _on_balance(self, data: Any) -> None:
        self._balance = Balance.model_validate(data)
        self._balance_ready.set()

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

    async def _on_open_order(self, data: Any) -> None:
        deal = Deal.model_validate(data)
        fut = self._pending_open.pop(deal.request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(deal)
        else:
            logger.debug("successopenOrder for unknown req=%s (external trade?)", deal.request_id)

    async def _on_close_order(self, data: Any) -> None:
        deals = data.get("deals", []) if isinstance(data, dict) else []
        for raw in deals:
            deal = Deal.model_validate(raw)
            fut = self._pending_close.pop(deal.request_id, None)
            if fut is not None and not fut.done():
                fut.set_result(deal)
            else:
                logger.debug("successcloseOrder for unknown req=%s", deal.request_id)

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
                # wake anyone awaiting the next price for this asset
                waiters = self._price_waiters.pop(asset, None)
                if waiters:
                    for fut in waiters:
                        if not fut.done():
                            fut.set_result(price)
            for cb in self._tick_callbacks:
                try:
                    res = cb(asset, price, timestamp)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    logger.exception("on_tick callback error")

    async def _on_opened_deals(self, data: Any) -> None:
        # Snapshot of currently open trades — replace our view.
        if not isinstance(data, list):
            return
        self._open_deals = {}
        for raw in data:
            try:
                deal = Deal.model_validate(raw)
                self._open_deals[deal.id] = deal
            except Exception:
                logger.debug("could not parse opened deal: %r", raw, exc_info=True)

    async def _on_closed_deals(self, data: Any) -> None:
        # History — accumulate (may arrive as a full list or just new closures).
        if not isinstance(data, list):
            return
        for raw in data:
            try:
                deal = Deal.model_validate(raw)
                self._closed_deals[deal.id] = deal
                self._open_deals.pop(deal.id, None)  # it's closed now
            except Exception:
                logger.debug("could not parse closed deal: %r", raw, exc_info=True)
