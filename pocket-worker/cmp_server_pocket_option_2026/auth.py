"""
cmp_server_pocket_option_2026.auth
=====================

Optional browser-based session capture. Instead of copying the SSID out of
DevTools by hand, you log in once in a real browser and this grabs the auth
frame automatically, saves it, and reuses it on later runs (no browser needed
until it expires).

This is a **convenience** feature, not a way to avoid detection: the captured
SSID is byte-for-byte the same token you'd copy manually, and the connection is
still a non-browser socket. It just makes refreshing the session painless.

Playwright is an **optional** dependency — it's imported lazily, so the rest of
the library works without it. To use auto-capture:

    pip install playwright
    playwright install chromium

Then either run this once to create a session:

    python -m cmp_server_pocket_option_2026.auth

…or set PO_AUTO_AUTH=1 and let the server open the browser when needed.

Security: the saved file under sessions/ contains your session token = full
account access. It's written owner-only where the OS supports it, and you should
keep sessions/ out of version control and never share it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cmp_server_pocket_option_2026.auth")

# The browser sends the real auth frame as: 42["auth",{"session":...,"isDemo":...}]
# (there's a decoy 42["auth",{"sessionToken":...}] — we require "isDemo" to skip it).
_AUTH_FRAME = re.compile(r'^42\[\s*"auth"')

LOGIN_URL = "https://pocketoption.com/en/login/"


class SessionManager:
    """Loads, saves, and (via a browser) captures a PocketOption session."""

    def __init__(self, sessions_dir: str = "sessions") -> None:
        self.dir = Path(sessions_dir)
        self.path = self.dir / "session.json"

    # ------------------------------------------------------------- storage

    def load(self) -> Optional[dict]:
        """Return the saved session dict, or None if there isn't a valid one."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and data.get("ssid") else None

    def save(self, data: dict) -> None:
        """Write the session to disk, owner-only, atomically.

        The temp file is created with mode 0600 *before* any bytes are written, so
        the session token is never briefly world-readable (which a write-then-chmod
        sequence would allow). ``os.replace`` then swaps it in atomically.
        """
        import tempfile

        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix="session.", suffix=".tmp")
        try:
            os.chmod(tmp, 0o600)  # POSIX; harmless no-op on Windows
        except OSError:
            pass
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        logger.info("Saved session to %s", self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------- capture

    async def capture(self, *, headless: bool = False, timeout: float = 300.0) -> dict:
        """Open a browser, wait for you to log in, and grab the auth frame.

        Returns ``{"ssid": <frame>, "user_agent": <ua>, "captured_at": <iso>}``.
        Requires Playwright (imported here so it stays optional).
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run:  pip install playwright  &&  "
                "playwright install chromium"
            ) from exc

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context()
            page = await context.new_page()

            loop = asyncio.get_running_loop()
            fut: "asyncio.Future[str]" = loop.create_future()

            def on_ws(ws) -> None:
                def on_frame(payload) -> None:
                    if (
                        isinstance(payload, str)
                        and _AUTH_FRAME.match(payload)
                        and '"isDemo"' in payload
                        and not fut.done()
                    ):
                        fut.set_result(payload)

                ws.on("framesent", on_frame)

            page.on("websocket", on_ws)
            await page.goto(LOGIN_URL)
            logger.info(
                "Log in to PocketOption in the browser window "
                "(email/password/captcha). I'll capture the session automatically."
            )

            try:
                ssid = await asyncio.wait_for(fut, timeout)
                user_agent = await page.evaluate("() => navigator.userAgent")
            finally:
                await browser.close()

        return {
            "ssid": ssid,
            "user_agent": user_agent,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    async def login(self, *, headless: bool = False, timeout: float = 300.0) -> dict:
        """Capture a fresh session via the browser and save it."""
        data = await self.capture(headless=headless, timeout=timeout)
        self.save(data)
        return data


async def resolve_ssid(*, auto_auth: bool = False, sessions_dir: str = "sessions") -> str:
    """Return an SSID for the client: use a saved session, else log in if allowed.

    Priority: a saved session file → (if ``auto_auth``) a fresh browser login →
    otherwise a helpful error.
    """
    sm = SessionManager(sessions_dir)
    saved = sm.load()
    if saved:
        return saved["ssid"]
    if auto_auth:
        data = await sm.login()
        return data["ssid"]
    raise RuntimeError(
        "No saved PocketOption session. Do one of: run "
        "`python -m cmp_server_pocket_option_2026.auth` once to log in, set PO_SSID directly, "
        "or set PO_AUTO_AUTH=1 to log in automatically."
    )


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        sm = SessionManager()
        data = await sm.login()
        # Show a masked confirmation — never print the token itself.
        frame = data["ssid"]
        is_demo = '"isDemo":1' in frame
        print(f"\nSession captured and saved to {sm.path}")
        print(f"Account type: {'DEMO' if is_demo else 'LIVE'}")
        print("You can now run the server/agents without setting PO_SSID.")

    asyncio.run(run())


if __name__ == "__main__":
    _main()
