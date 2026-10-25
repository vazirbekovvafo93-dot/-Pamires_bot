"""
cmp_server_pocket_option_2026._transport
===========================

Low-level async transport for the PocketOption WebSocket API.

This layer owns the *connection only*: Socket.IO / Engine.IO framing (delegated
to ``python-socketio``), the auth handshake, an app-level keep-alive, multi-region
failover and reconnection. It knows nothing about candles, deals or strategy —
that lives in ``client.py``, which receives every inbound event through
:meth:`Transport.set_dispatcher`.

Why it looks like this
----------------------
* **We do not hand-roll the Socket.IO frame protocol** (``0/40/2/3/42/451``).
  ``python-socketio``'s ``AsyncClient`` does that, plus the heartbeat and
  reconnection with exponential backoff + jitter. Hand-rolling those is exactly
  what made the old v4 codebase fragile (spin-locks, flag variables, a new event
  loop per send).
* **TLS verification is ON by default.** The old code used ``CERT_NONE``, which
  exposed the session token to a man-in-the-middle.
* **The captured SSID is forwarded verbatim.** A browser-captured SSID already
  contains the exact auth object the server wants (``session``, ``isDemo``,
  ``uid``, ``platform``, ``isFastHistory``, ``isOptimized``, ...). Forwarding it
  as-is means we never fall out of sync when PocketOption adds a field.

Install the runtime dependency with::

    pip install "python-socketio[asyncio_client]"
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import os
import random
from collections import Counter
from typing import Any, Awaitable, Callable, Optional

import socketio

from .constants import (
    DEFAULT_ORIGIN,
    DEFAULT_USER_AGENT,
    DEMO_REGIONS,
    LIVE_REGIONS,
    PING_INTERVAL,
    Region,
)
from .exceptions import AuthenticationError, ConnectionFailedError, NotAuthorizedError

logger = logging.getLogger("cmp_server_pocket_option_2026.transport")

# A dispatcher receives every inbound event: (event_name, data) -> awaitable.
Dispatcher = Callable[[str, Any], Awaitable[None]]


def _decode(obj: Any) -> Any:
    """Decode PocketOption's binary (``451-``) payloads: bytes -> parsed JSON.

    Socket.IO hands these events' bodies over as ``bytes`` containing JSON text.
    We decode recursively so handlers always receive real dicts/lists, never
    raw bytes. Non-bytes values pass through unchanged (idempotent).
    """
    if isinstance(obj, (bytes, bytearray)):
        try:
            text = bytes(obj).decode("utf-8")
        except Exception:
            return obj
        try:
            return _decode(json.loads(text))
        except Exception:
            return text
    if isinstance(obj, dict):
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


class _DumpRecorder:
    """Writes every event (with its full, binary-decoded body) to a JSONL file.

    Socket.IO delivers PocketOption's ``451-`` messages with their payload as
    binary attachments; this recorder decodes those bytes back into JSON so the
    dump holds real bodies, not ``_placeholder`` stubs. Secrets are stripped.
    """

    _SECRET_KEYS = {"session", "uid", "sessionToken", "token", "sid"}

    def __init__(self, path: str, *, full_stream: bool = False, stream_cap: int = 5) -> None:
        self._full_stream = full_stream
        self._stream_cap = stream_cap
        self._counts: Counter = Counter()
        self._fh = open(path, "w", encoding="utf-8")

    def record(self, direction: str, event: str, data: Any) -> None:
        self._counts[event] += 1
        # updateStream is a flood of quotes; keep only the first few by default.
        if event == "updateStream" and not self._full_stream and self._counts[event] > self._stream_cap:
            return
        line = {
            "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "dir": direction,
            "event": event,
            "data": self._redact(self._decode_bytes(data)),
        }
        self._fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def _decode_bytes(self, obj: Any) -> Any:
        if isinstance(obj, (bytes, bytearray)):
            try:
                text = bytes(obj).decode("utf-8")
            except Exception:
                return {"__bytes_b64__": base64.b64encode(bytes(obj)).decode("ascii")}
            try:
                return self._decode_bytes(json.loads(text))
            except Exception:
                return text
        if isinstance(obj, dict):
            return {k: self._decode_bytes(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._decode_bytes(v) for v in obj]
        return obj

    def _redact(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: ("***" if k in self._SECRET_KEYS else self._redact(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._redact(v) for v in obj]
        return obj

    def summary(self) -> "list[tuple[str, int]]":
        return self._counts.most_common()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def parse_ssid(ssid: str) -> "tuple[dict, bool, Optional[int]]":
    """Parse a browser-captured SSID frame into its auth object.

    Accepts the raw frame copied from the browser / DevTools, e.g.::

        42["auth",{"session":"...","isDemo":1,"uid":123,"platform":1,...}]

    Returns ``(auth_object, is_demo, uid)``. The auth object is returned
    unchanged so it can be forwarded to the server verbatim.

    Raises :class:`AuthenticationError` if the string is not a valid auth frame.
    """
    raw = ssid.strip()
    if raw.startswith("42"):
        raw = raw[2:]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthenticationError(f"SSID is not valid JSON: {exc}") from exc

    if isinstance(payload, list) and len(payload) == 2 and payload[0] == "auth":
        auth = payload[1]
    elif isinstance(payload, dict):
        auth = payload  # already the bare auth object
    else:
        raise AuthenticationError("SSID does not contain an 'auth' payload")

    if not isinstance(auth, dict) or "session" not in auth:
        raise AuthenticationError("SSID auth payload is missing 'session'")

    return auth, bool(auth.get("isDemo", 0)), auth.get("uid")


class Transport:
    """Owns the live connection to PocketOption and nothing else."""

    def __init__(
        self,
        ssid: str,
        *,
        demo: Optional[bool] = None,
        regions: "Optional[list[Region]]" = None,
        ssl_verify: bool = True,
        ping_interval: float = PING_INTERVAL,
        origin: str = DEFAULT_ORIGIN,
        user_agent: str = DEFAULT_USER_AGENT,
        reconnection: bool = True,
        reconnection_attempts: int = 0,   # 0 = unlimited
        reconnection_delay: float = 1.0,
        reconnection_delay_max: float = 8.0,
        randomization_factor: float = 0.5,
        shuffle_regions: bool = True,
        dump_path: Optional[str] = None,
        dump_full_stream: bool = False,
    ) -> None:
        self._auth, parsed_demo, self.uid = parse_ssid(ssid)
        self.is_demo = parsed_demo if demo is None else demo
        if demo is not None and demo != parsed_demo:
            logger.warning(
                "demo=%s overrides isDemo=%s from the SSID — make sure that is intended",
                demo,
                parsed_demo,
            )

        self._regions = list(regions) if regions else list(
            DEMO_REGIONS if self.is_demo else LIVE_REGIONS
        )
        self._shuffle_regions = shuffle_regions
        self._headers = {"Origin": origin, "User-Agent": user_agent}
        self._ping_interval = ping_interval

        self._sio = socketio.AsyncClient(
            reconnection=reconnection,
            reconnection_attempts=reconnection_attempts,
            reconnection_delay=reconnection_delay,
            reconnection_delay_max=reconnection_delay_max,
            randomization_factor=randomization_factor,
            ssl_verify=ssl_verify,
            handle_sigint=False,   # never hijack the host application's Ctrl+C
            logger=False,
            engineio_logger=False,
        )

        self._authenticated = asyncio.Event()
        self._auth_failed = asyncio.Event()
        self._dispatcher: Optional[Dispatcher] = None
        self._ping_task: Optional[asyncio.Task] = None
        self.region: Optional[Region] = None
        self._dump = _DumpRecorder(dump_path, full_stream=dump_full_stream) if dump_path else None

        self._sio.on("connect", self._on_connect)
        self._sio.on("disconnect", self._on_disconnect)
        self._sio.on("*", self._on_event)

    # ------------------------------------------------------------------ API

    def set_dispatcher(self, dispatcher: Dispatcher) -> None:
        """Register the callback that receives every inbound event."""
        self._dispatcher = dispatcher

    @property
    def connected(self) -> bool:
        """True only when the socket is up *and* the session is authenticated."""
        return self._sio.connected and self._authenticated.is_set()

    async def connect(self, *, auth_timeout: float = 8.0, connect_timeout: float = 8.0) -> Region:
        """Connect to the first reachable region that authenticates.

        Both connection-level failures *and* auth timeouts fall through to the
        next region — some endpoints accept the socket but never complete the
        handshake. We only fast-fail (raise :class:`NotAuthorizedError`) when the
        server *explicitly* rejects the session, since that is server-wide and
        retrying other regions would be pointless.
        """
        regions = list(self._regions)
        if self._shuffle_regions:
            random.shuffle(regions)

        last_error: Optional[Exception] = None
        connected_any = False
        for region in regions:
            self._authenticated.clear()
            self._auth_failed.clear()
            try:
                await self._sio.connect(
                    region.value,
                    headers=self._headers,
                    transports=["websocket"],
                    socketio_path="socket.io",
                    wait=True,
                    wait_timeout=connect_timeout,
                )
            except Exception as exc:  # connection-level → try the next region
                last_error = exc
                logger.warning("Connect to %s failed: %s", region.name, exc)
                await self._safe_disconnect()
                continue

            connected_any = True
            result = await self._await_auth(auth_timeout)
            if result == "ok":
                self.region = region
                logger.info(
                    "Connected to %s (%s account)",
                    region.name,
                    "demo" if self.is_demo else "live",
                )
                return region

            await self._safe_disconnect()
            if result == "rejected":
                raise NotAuthorizedError(
                    "The server rejected the session — the SSID is expired or "
                    "invalid. Re-capture a fresh one from the browser."
                )
            # timeout → this endpoint is not completing the handshake; try next
            logger.warning(
                "No 'successauth' from %s within %ss — trying the next region",
                region.name, auth_timeout,
            )
            last_error = AuthenticationError(f"no successauth from {region.name}")

        if not connected_any:
            raise ConnectionFailedError(
                f"Could not connect to any of {len(regions)} region(s)"
            ) from last_error
        raise AuthenticationError(
            "Connected, but no region returned 'successauth'. If this persists, "
            "the SSID is most likely expired — re-capture it from the browser."
        ) from last_error

    async def _await_auth(self, timeout: float) -> str:
        """Wait for either successful auth or an explicit rejection.

        Returns ``"ok"``, ``"rejected"`` or ``"timeout"``.
        """
        ok = asyncio.ensure_future(self._authenticated.wait())
        bad = asyncio.ensure_future(self._auth_failed.wait())
        try:
            await asyncio.wait(
                {ok, bad}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (ok, bad):
                if not task.done():
                    task.cancel()
        if self._auth_failed.is_set():
            return "rejected"
        if self._authenticated.is_set():
            return "ok"
        return "timeout"

    async def emit(self, event: str, data: Any = None) -> None:
        """Send an event to the server.

        Pydantic models are serialized with field aliases (camelCase wire form).
        """
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json", by_alias=True)
        if self._dump is not None:
            self._dump.record("out", event, data)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("-> %s %s", event, _redact(event, data))
        await self._sio.emit(event, data)

    async def wait(self) -> None:
        """Block until the connection closes (handy for long-running bots)."""
        await self._sio.wait()

    async def disconnect(self) -> None:
        """Cleanly tear the connection down."""
        self._cancel_ping()
        await self._safe_disconnect()
        if self._dump is not None:
            self._dump.close()

    def dump_summary(self) -> "list[tuple[str, int]]":
        """Return (event, count) pairs seen so far, most frequent first."""
        return self._dump.summary() if self._dump is not None else []

    # -------------------------------------------------------------- handlers

    async def _on_connect(self) -> None:
        # PocketOption requires (re-)auth on every (re)connection.
        logger.debug("Socket connected; sending auth")
        await self._sio.emit("auth", self._auth)

    async def _on_disconnect(self) -> None:
        logger.info("Socket disconnected")
        self._authenticated.clear()
        self._cancel_ping()
        if self._dispatcher:
            await self._dispatcher("disconnect", None)

    async def _on_event(self, event: str, data: Any = None) -> None:
        data = _decode(data)  # PocketOption sends 451- bodies as bytes; parse them
        if event == "successauth":
            logger.debug("Session authenticated")
            self._authenticated.set()
            self._start_ping()
        elif "notauthoriz" in event.lower() or "unauthoriz" in event.lower():
            # Server explicitly rejected the session (bad/expired SSID).
            logger.debug("Auth rejected by server: %s", event)
            self._auth_failed.set()
        if self._dump is not None:
            self._dump.record("in", event, data)
        # Forward everything to the client layer for routing.
        if self._dispatcher:
            try:
                await self._dispatcher(event, data)
            except Exception:
                logger.exception("Dispatcher raised on event %r", event)

    # ------------------------------------------------------------ keep-alive

    def _start_ping(self) -> None:
        self._cancel_ping()
        self._ping_task = asyncio.create_task(self._ping_loop())

    def _cancel_ping(self) -> None:
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        self._ping_task = None

    async def _ping_loop(self) -> None:
        # Engine.IO's 2/3 heartbeat is handled by socketio; this is the
        # PocketOption app-level keep-alive ("42[\"ps\"]").
        try:
            while True:
                await asyncio.sleep(self._ping_interval)
                if self._sio.connected:
                    await self._sio.emit("ps")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Ping loop stopped unexpectedly")

    async def _safe_disconnect(self) -> None:
        try:
            if self._sio.connected:
                await self._sio.disconnect()
        except Exception:
            logger.debug("Error during disconnect", exc_info=True)


def _redact(event: str, data: Any) -> Any:
    """Never log the session token or uid."""
    if event == "auth" and isinstance(data, dict):
        return {**data, "session": "***", "uid": "***"}
    return data


if __name__ == "__main__":
    # Smoke test / protocol recorder.
    #
    # Never hardcode the SSID — read it from the environment:
    #     $env:PO_SSID='42["auth",{...}]'        (PowerShell)
    #
    # Optional switches:
    #     $env:PO_DUMP='1'          record every event (full body) to dump.jsonl
    #     $env:PO_LISTEN='600'      seconds to stay connected (default 15)
    #     $env:PO_DUMP_STREAM='1'   also record every updateStream tick (huge!)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def _smoke_test() -> None:
        ssid = os.environ.get("PO_SSID")
        if not ssid:
            raise SystemExit("Set PO_SSID to your browser-captured SSID frame first.")

        dump = os.environ.get("PO_DUMP") == "1"
        listen = int(os.environ.get("PO_LISTEN", "15"))
        full_stream = os.environ.get("PO_DUMP_STREAM") == "1"

        transport = Transport(
            ssid,  # demo/live auto-detected from the SSID
            dump_path="dump.jsonl" if dump else None,
            dump_full_stream=full_stream,
        )

        async def show(event: str, data: Any) -> None:
            if event != "updateStream":  # very chatty
                logger.info("<- %s", event)

        transport.set_dispatcher(show)
        region = await transport.connect()
        if dump:
            logger.info(
                "Recording to dump.jsonl on %s. Go click around the platform for %ss "
                "(open a trade, wait for it to close, switch assets/timeframes, open history)...",
                region.name, listen,
            )
        else:
            logger.info("OK — authenticated on %s. Listening for %ss...", region.name, listen)

        try:
            await asyncio.sleep(listen)
        except KeyboardInterrupt:
            pass
        await transport.disconnect()

        if dump:
            logger.info("--- events captured (event: count) ---")
            for event, count in transport.dump_summary():
                logger.info("  %-30s %d", event, count)
            logger.info("Saved to dump.jsonl (session/uid already redacted).")
        logger.info("Done.")

    asyncio.run(_smoke_test())
