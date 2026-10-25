import asyncio
import json
import logging
import os
import threading
from urllib.parse import quote, unquote, urlsplit

from cmp_server_pocket_option_2026.client import AsyncPocketOption

_log = logging.getLogger("pocket_option_cmp")
_proxy_patch_lock = threading.Lock()
_proxy_patch_done = False


def _parse_ssid_local(raw: str):
    value = (raw or "").strip()
    if value.startswith("42"):
        value = value[2:].strip()
    payload = json.loads(value)
    if isinstance(payload, list) and len(payload) == 2 and payload[0] == "auth":
        auth = payload[1]
    elif isinstance(payload, dict):
        auth = payload
    else:
        raise ValueError("SSID does not contain an auth payload")
    if not isinstance(auth, dict) or not str(auth.get("session") or "").strip():
        raise ValueError("SSID auth payload is missing session")
    return auth, bool(auth.get("isDemo", 0)), auth.get("uid")


def _effective_proxy_url():
    host = (os.getenv("PO_PROXY_HOST") or "").strip()
    port = (os.getenv("PO_PROXY_PORT") or "").strip()
    user = (os.getenv("PO_PROXY_USERNAME") or "").strip()
    password = os.getenv("PO_PROXY_PASSWORD") or ""
    scheme = (os.getenv("PO_PROXY_SCHEME") or "http").strip().lower()
    if host and port:
        if scheme not in ("http", "https", "socks5", "socks5h"):
            raise ValueError("unsupported proxy scheme")
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user or password else ""
        return f"{scheme}://{auth}{host}:{int(port)}"
    raw = (os.getenv("PO_PROXY_URL") or os.getenv("PO_PROXY") or "").strip()
    if not raw:
        return ""
    p = urlsplit(raw if "://" in raw else "http://" + raw)
    if not p.hostname or not p.port:
        raise ValueError("invalid proxy URL")
    user = unquote(p.username or "")
    password = unquote(p.password or "")
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user or password else ""
    return f"{p.scheme.lower()}://{auth}{p.hostname}:{p.port}"


def _proxy_configured():
    try:
        return bool(_effective_proxy_url())
    except Exception:
        return False


def _install_socketio_proxy():
    global _proxy_patch_done
    proxy = _effective_proxy_url()
    if not proxy or _proxy_patch_done:
        return
    with _proxy_patch_lock:
        if _proxy_patch_done:
            return
        import aiohttp
        import socketio
        from aiohttp_socks import ProxyConnector
        original = socketio.AsyncClient
        p = urlsplit(proxy)
        scheme = (p.scheme or "http").lower()

        class ProxyAsyncClient(original):
            def __init__(self, *args, **kwargs):
                self._pms_proxy_session = None
                if "http_session" not in kwargs:
                    if scheme in ("socks5", "socks5h"):
                        connector = ProxyConnector.from_url(proxy)
                        self._pms_proxy_session = aiohttp.ClientSession(connector=connector, trust_env=False)
                    else:
                        # Explicitly route the socketio/aiohttp session through Decodo.
                        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                            os.environ[key] = proxy
                        self._pms_proxy_session = aiohttp.ClientSession(trust_env=True)
                    kwargs["http_session"] = self._pms_proxy_session
                super().__init__(*args, **kwargs)

            async def disconnect(self):
                try:
                    return await super().disconnect()
                finally:
                    session = self._pms_proxy_session
                    self._pms_proxy_session = None
                    if session is not None and not session.closed:
                        await session.close()

        socketio.AsyncClient = ProxyAsyncClient
        _proxy_patch_done = True
        _log.info("PO_DIAG proxy_transport=enabled scheme=%s host=%s port=%s", scheme, p.hostname, p.port)


def _normalize_ssid(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("captured Pocket Option auth is empty")
    if text.startswith("42"):
        auth, _demo, _uid = _parse_ssid_local(text)
        if auth.get("session"):
            return text
    auth, demo, uid = _parse_ssid_local(text)
    normalized = dict(auth)
    normalized["session"] = str(normalized.get("session") or normalized.get("sessionToken") or "").strip()
    if not normalized["session"]:
        raise ValueError("SSID auth payload is missing session")
    normalized.setdefault("isDemo", int(demo))
    if normalized.get("uid") is None and uid is not None:
        normalized["uid"] = uid
    if normalized.get("uid") is None and os.getenv("PO_UID"):
        try:
            normalized["uid"] = int(os.getenv("PO_UID").strip())
        except ValueError:
            normalized["uid"] = os.getenv("PO_UID").strip()
    normalized.setdefault("platform", 2)
    return "42" + json.dumps(["auth", normalized], separators=(",", ":"), ensure_ascii=False)


class CmpPocketOption:
    """Synchronous, read-only adapter around AsyncPocketOption. No trading is exposed."""
    def __init__(self, ssid: str, *, use_proxy: bool = False):
        self.ssid = _normalize_ssid(ssid)
        _, self.is_demo, self.uid = _parse_ssid_local(self.ssid)
        self.use_proxy = bool(use_proxy)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="po-cmp-loop")
        self._ready = threading.Event()
        self._failed = threading.Event()
        self._stopped = threading.Event()
        self._error = None
        self._client = None
        self._start_future = None
        self._thread.start()
        self._start_future = self._submit(self._start())

    def _run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(self._loop)
                       if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._stopped.set()

    def _submit(self, coro):
        if self._stopped.is_set() or self._loop.is_closed():
            raise RuntimeError("Pocket Option event loop is unavailable")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _start(self):
        try:
            if self.use_proxy:
                _install_socketio_proxy()
            self._client = AsyncPocketOption(
                self.ssid,
                demo=self.is_demo,
                origin=os.getenv("PO_ORIGIN", "https://m.pocketoption.com"),
                user_agent=os.getenv("PO_USER_AGENT") or None,
                reconnection=True,
                shuffle_regions=True,
            )
            region = await self._client.connect(ready_timeout=20)
            self._ready.set()
            _log.info("PO_DIAG auth=SUCCESS mode=%s uid=%s region=%s transport=%s", "DEMO" if self.is_demo else "REAL", self.uid, getattr(region, "name", region), "PROXY" if self.use_proxy else "DIRECT")
        except asyncio.CancelledError:
            # A cancelled connect must still release the websocket/session.
            if self._client is not None:
                try:
                    await asyncio.shield(self._client.close())
                except Exception:
                    pass
            raise
        except Exception as exc:
            self._error = str(exc)
            self._failed.set()
            _log.error("PO_DIAG auth=FAILED reason=%s", exc)
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:
                    pass

    def connect(self, timeout=40):
        if self._ready.wait(timeout):
            return True, None
        if self._start_future is not None and self._start_future.done():
            try:
                self._start_future.result()
            except Exception as exc:
                self._error = str(exc)
        return False, self._error or "Pocket Option connection/authentication timeout"

    def check_connect(self):
        # connect()'s ready event is authoritative. The underlying client can
        # transiently flip connected=False during its post-handshake transition.
        return bool(self._ready.is_set() and self._client is not None and not self._failed.is_set() and not self._stopped.is_set())

    def is_time_synced(self):
        return bool(self.check_connect() and self.get_assets())

    def get_assets(self):
        if not self.check_connect():
            return {}
        try:
            assets = self._client.assets(active_only=False)
            out = {}
            for asset in assets:
                name = getattr(asset, "symbol", None) or getattr(asset, "name", None)
                if name:
                    out[str(name)] = {"name": str(name), "payout": getattr(asset, "payout", None), "is_active": getattr(asset, "is_active", True)}
            return out
        except Exception as exc:
            _log.warning("PO_DIAG assets=FAILED reason=%s", exc)
            return {}

    def get_historical_candles(self, symbol, period, offset=None, count_request=200):
        if not self.check_connect():
            return []
        try:
            count = max(20, min(int(count_request or 200), 1000))
            return self._submit(self._client.candles(str(symbol), int(period), count=count)).result(timeout=20)
        except Exception as exc:
            _log.warning("PO_DIAG candles=FAILED symbol=%s reason=%s", symbol, exc)
            return []

    def disconnect_websocket(self):
        if self._stopped.is_set():
            return
        try:
            if self._client is not None:
                self._submit(self._close()).result(timeout=15)
        except Exception as exc:
            _log.warning("PO_DIAG disconnect=FAILED reason=%s", exc)
        finally:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
            self._thread.join(timeout=15)

    async def _close(self):
        if self._client is not None:
            await self._client.close()


def create(ssid: str):
    return CmpPocketOption(ssid, use_proxy=_proxy_configured())
