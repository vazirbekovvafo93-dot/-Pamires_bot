"""Read-only Pocket Option short-timeframe feed built from live ticks.

The selected S5/S10/S15/S30 interval is built from Pocket Option's live
updateStream ticks. No trade operation is called.
"""
from collections import defaultdict, deque
import asyncio
import concurrent.futures
import inspect
import threading
import time

import pocket_feed_cmp as _po

_LOCK = threading.RLock()
_BUFFERS = defaultdict(lambda: deque(maxlen=5000))
_LISTENERS = set()


def _normalize_ts(value):
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return time.time()
    if ts > 10_000_000_000:
        ts /= 1000.0
    return ts


def _call_client_on_loop(api, fn, timeout=10):
    """Call a client method on CmpPocketOption's asyncio loop safely.

    This is a compatibility bridge for the current adapter while keeping all
    market-data operations read-only. It supports both synchronous and async
    methods exposed by the installed Pocket Option client.
    """
    loop = getattr(api, "_loop", None)
    client = getattr(api, "_client", None)
    if loop is None or client is None or loop.is_closed():
        raise RuntimeError("Pocket Option client event loop is unavailable")

    result = concurrent.futures.Future()

    def finish(value=None, error=None):
        if result.done():
            return
        if error is not None:
            result.set_exception(error)
        else:
            result.set_result(value)

    def runner():
        try:
            value = fn(client)
            if inspect.isawaitable(value):
                task = asyncio.ensure_future(value, loop=loop)

                def done_callback(done):
                    try:
                        finish(done.result())
                    except BaseException as exc:
                        finish(error=exc)

                task.add_done_callback(done_callback)
            else:
                finish(value)
        except BaseException as exc:
            finish(error=exc)

    loop.call_soon_threadsafe(runner)
    return result.result(timeout=timeout)


def _install_listener(api, symbol):
    key = (id(api), symbol)
    with _LOCK:
        if key in _LISTENERS:
            return
        _LISTENERS.add(key)

    def on_tick(asset, price, timestamp):
        if asset is not None and str(asset) != str(symbol):
            return
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        ts = _normalize_ts(timestamp)
        with _LOCK:
            _BUFFERS[symbol].append((ts, p))

    # The adapter currently exposes the underlying async client internally;
    # bridge registration onto its own event-loop thread instead of calling
    # an adapter method that does not exist in older deployments.
    _call_client_on_loop(api, lambda client: client.on_tick(on_tick))


def _resolve_symbol(api, requested):
    requested = str(requested or '').strip()
    assets = api.get_assets() or {}
    upper = requested.upper()
    candidates = [requested]
    if upper.endswith(' OTC'):
        base = requested[:-4].strip()
        compact = base.replace('/', '').replace(' ', '')
        candidates += [compact + '_otc', compact + '_OTC']
    elif upper.endswith('_OTC'):
        candidates += [requested.lower(), requested.upper()]
    else:
        compact = requested.replace('/', '').replace(' ', '')
        candidates += [compact, compact + '_otc', compact + '_OTC']
    for candidate in candidates:
        if candidate in assets:
            return candidate
    wanted = {str(x).upper() for x in candidates}
    for name in assets:
        if str(name).upper() in wanted:
            return str(name)
    return None


def collect_ticks(requested, wait_seconds=8):
    if not _po.configured():
        raise RuntimeError('PO_SSID is not configured')
    api = _po.client()
    if api is None or not api.check_connect():
        raise RuntimeError('Pocket Option client is not connected')
    symbol = _resolve_symbol(api, requested)
    if not symbol:
        raise RuntimeError('Pocket Option asset not found: %s' % requested)
    _install_listener(api, symbol)

    # Subscribe through the same event-loop bridge. No trade API is called.
    _call_client_on_loop(api, lambda client: client.subscribe(symbol))

    started = time.time()
    deadline = started + max(1, min(int(wait_seconds), 30))
    while time.time() < deadline:
        with _LOCK:
            fresh = [x for x in _BUFFERS[symbol] if x[0] >= started - 0.25]
        if len(fresh) >= 2:
            break
        time.sleep(0.05)
    # Keep the rolling in-memory history so later requests can build
    # multiple closed short-timeframe candles instead of only the last few
    # ticks observed during this single call. The buffer is process-local and
    # resets on restart; callers must still validate that enough history exists.
    with _LOCK:
        rows = list(_BUFFERS[symbol])
    rows.sort(key=lambda x: x[0])
    return symbol, rows


def bucket(rows, seconds=5):
    seconds = int(seconds)
    if seconds not in (5, 10, 15, 30):
        raise ValueError('short timeframe must be 5, 10, 15 or 30 seconds')
    out = {}
    for ts, price in rows:
        start = int(ts // seconds) * seconds
        item = out.setdefault(
            start,
            {'time': start, 'open': price, 'high': price, 'low': price, 'close': price, 'ticks': 0},
        )
        item['high'] = max(item['high'], price)
        item['low'] = min(item['low'], price)
        item['close'] = price
        item['ticks'] += 1
    return [out[k] for k in sorted(out)]


def diagnostic(requested='AUD/USD OTC', wait_seconds=8):
    started = time.time()
    symbol, rows = collect_ticks(requested, wait_seconds=wait_seconds)
    bars = bucket(rows, 5)
    span = (rows[-1][0] - rows[0][0]) if len(rows) >= 2 else 0
    ok = len(rows) >= 2 and span > 0
    return {
        'ok': ok,
        'trading': False,
        'requested': requested,
        'resolved_symbol': symbol,
        'ticks': len(rows),
        'span_seconds': round(span, 3),
        'five_second_buckets': len(bars),
        'latest_bucket': bars[-1] if bars else None,
        'sample_ticks': [{'time': round(ts, 3), 'price': price} for ts, price in rows[-10:]],
        'elapsed_seconds': round(time.time() - started, 3),
        'source': 'POCKET_OPTION_UPDATE_STREAM',
        'native_candle_history': False,
        'message': (
            'Real Pocket Option ticks received; 5-second buckets can be constructed read-only.'
            if ok else
            'No live Pocket Option ticks received during diagnostic window.'
        ),
    }
