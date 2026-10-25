import json
import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger("pocket_auto_login")


def _diag(message: str) -> None:
    print(str(message), flush=True)


def _auth_text_candidates(text: str):
    text = (text or "").strip()
    if not text:
        return []
    candidates = []
    if text.startswith("42"):
        candidates.append(text[2:])
    if text.startswith("[") or text.startswith("{"):
        candidates.append(text)
    return candidates


def _auth_frame(value):
    for candidate in _auth_text_candidates(str(value or "")):
        try:
            payload = json.loads(candidate)
        except Exception:
            continue

        auth = None
        if isinstance(payload, list) and len(payload) >= 2 and payload[0] == "auth" and isinstance(payload[1], dict):
            auth = dict(payload[1])
        elif isinstance(payload, dict) and payload.get("event") == "auth" and isinstance(payload.get("data"), dict):
            auth = dict(payload["data"])
        elif isinstance(payload, dict):
            auth = dict(payload)

        if isinstance(auth, dict) and auth.get("session"):
            auth.setdefault("isDemo", 1)
            auth.setdefault("platform", 2)
            auth.setdefault("isFastHistory", True)
            auth.setdefault("isOptimized", True)
            return '42["auth",' + json.dumps(auth, separators=(",", ":"), ensure_ascii=False) + "]"
    return None


def _deep_auth(value, depth=0):
    if depth > 8:
        return None
    if isinstance(value, str):
        found = _auth_frame(value)
        if found:
            return found
        try:
            return _deep_auth(json.loads(value), depth + 1)
        except Exception:
            return None
    if isinstance(value, dict):
        if value.get("session"):
            obj = dict(value)
            obj.setdefault("isDemo", 1)
            obj.setdefault("platform", 2)
            obj.setdefault("isFastHistory", True)
            obj.setdefault("isOptimized", True)
            return '42["auth",' + json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "]"
        for child in value.values():
            found = _deep_auth(child, depth + 1)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _deep_auth(child, depth + 1)
            if found:
                return found
    return None


def _storage_auth(page):
    try:
        values = page.evaluate(
            """() => {
                const out = [];
                for (const store of [window.localStorage, window.sessionStorage]) {
                    for (let i = 0; i < store.length; i++) {
                        const key = store.key(i);
                        try {
                            const value = store.getItem(key);
                            if (value) out.push([key, value]);
                        } catch (e) {}
                    }
                }
                return out;
            }"""
        )
    except Exception:
        return None

    for key, value in values or []:
        found = _deep_auth(value)
        if found:
            _diag(f"PO_DIAG auth=FOUND source=browser_storage key={str(key)[:80]}")
            return found
    return None


def _cookie_auth(context):
    try:
        cookies = context.cookies()
    except Exception:
        return None

    for cookie in cookies:
        value = str(cookie.get("value") or "").strip()
        if not value:
            continue
        found = _deep_auth(value)
        if found:
            _diag(f"PO_DIAG auth=FOUND source=cookie name={str(cookie.get('name') or '')[:80]}")
            return found
    return None


def _ensure_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        try:
            _diag("PO_DIAG browser=INSTALLING")
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=420)
            _diag("PO_DIAG browser=READY")
            return True
        except Exception as exc:
            _diag(f"PO_DIAG browser=INSTALL_FAILED reason={str(exc)[:220]}")
            return False


def _fill(page, selectors, value):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.fill(value)
                return True
        except Exception:
            pass
    return False


def _click(page, selectors):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.click(timeout=5000)
                return True
        except Exception:
            pass
    return False


def _try_page(pw, login_url, email, password, timeout):
    browser = None
    context = None
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=os.getenv("PO_USER_AGENT") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        captured = {"ssid": None}

        def on_frame(frame):
            if not captured["ssid"]:
                captured["ssid"] = _auth_frame(frame)
                if captured["ssid"]:
                    _diag("PO_DIAG auth=FOUND source=websocket_frame")

        def attach_ws(ws):
            ws.on("framereceived", on_frame)
            ws.on("framesent", on_frame)

        page.on("websocket", attach_ws)
        _diag(f"PO_DIAG login=OPEN url={login_url} transport=DIRECT")
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:
            _diag(f"PO_DIAG login=OPEN_FAILED reason={str(exc)[:180]}")
            return None

        page.wait_for_timeout(2000)
        email_ok = _fill(page, ['input[name="email"]', 'input[type="email"]', 'input[name="login"]', 'input[autocomplete="username"]', 'input[placeholder*="mail" i]'], email)
        password_ok = _fill(page, ['input[name="password"]', 'input[type="password"]', 'input[autocomplete="current-password"]', 'input[placeholder*="password" i]'], password)
        _diag(f"PO_DIAG login=FIELDS email={email_ok} password={password_ok}")
        if not email_ok or not password_ok:
            return None

        clicked = _click(page, ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Login")', 'button:has-text("Log in")', 'button:has-text("Войти")'])
        if not clicked:
            try:
                page.locator('input[type="password"]').press("Enter")
            except Exception:
                pass

        _diag(f"PO_DIAG login=SUBMIT clicked={clicked}")
        deadline = time.time() + timeout
        while time.time() < deadline and not captured["ssid"]:
            captured["ssid"] = _storage_auth(page) or _cookie_auth(context)
            if captured["ssid"]:
                break
            page.wait_for_timeout(750)

        if captured["ssid"]:
            _diag("PO_DIAG login=OK auth=FOUND")
            return captured["ssid"]

        try:
            body = " ".join(page.locator("body").inner_text(timeout=3000).split())[:250]
        except Exception:
            body = ""
        _diag(f"PO_DIAG login=WAITING_AUTH final_url={page.url} body={body}")
        return None
    except Exception as exc:
        _diag(f"PO_DIAG login=FAILED reason={str(exc)[:240]}")
        return None
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass


def get_ssid() -> str | None:
    email = os.getenv("PO_LOGIN_EMAIL", "").strip()
    password = os.getenv("PO_LOGIN_PASSWORD", "")
    if not email or not password:
        _diag("PO_DIAG login=SKIPPED reason=CREDENTIALS_NOT_CONFIGURED")
        return None

    _diag("PO_DIAG login=BEGIN")
    if not _ensure_browser():
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _diag("PO_DIAG browser=FAILED reason=PLAYWRIGHT_UNAVAILABLE")
        return None

    timeout = max(30, int(os.getenv("PO_LOGIN_TIMEOUT", "90")))
    urls = []
    configured_url = os.getenv("PO_LOGIN_URL", "").strip()
    if configured_url:
        urls.append(configured_url)
    for url in ("https://pocketoption.com/en/login/", "https://m.pocketoption.com/en/login/", "https://po.trade/en/login/"):
        if url not in urls:
            urls.append(url)

    with sync_playwright() as pw:
        for url in urls:
            found = _try_page(pw, url, email, password, timeout)
            if found:
                return found

    _diag("PO_DIAG login=END reason=SESSION_NOT_RECOVERED")
    return None
