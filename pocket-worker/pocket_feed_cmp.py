import hashlib
import logging
import os
import threading
import time
import json
from urllib.parse import unquote

_log = logging.getLogger("pocket_feed")
_client = None
_client_transport = None
_lock = threading.Lock()
_assets_cache = {"items": None, "updated": 0.0}
_assets_ttl = 2.0
_last_auth_error = None
_last_auth_error_at = 0.0
_AUTH_RETRY_COOLDOWN = 120.0


def _diag(message):
    _log.info(str(message))


def _auto_auth_enabled():
    return bool(os.getenv("PO_LOGIN_EMAIL") and os.getenv("PO_LOGIN_PASSWORD"))


def _manual_ssid():
    return (os.getenv("PO_SSID") or "").strip()


def _auth_mode():
    if _manual_ssid(): return "PO_SSID"
    if _auto_auth_enabled(): return "AUTO_BROWSER"
    return "NONE"


def _proxy_configured_safe():
    host=(os.getenv("PO_PROXY_HOST") or "").strip(); port=(os.getenv("PO_PROXY_PORT") or "").strip(); raw=(os.getenv("PO_PROXY_URL") or os.getenv("PO_PROXY") or "").strip()
    return bool((host and port) or raw)


def configured(): return bool(_manual_ssid() or _auto_auth_enabled())


def _classify_error(error):
    text=str(error or "").lower()
    if any(x in text for x in ("captcha","anti-bot","human verification","cloudflare","verify you are human","проверка человека","recaptcha")): return "CAPTCHA"
    return "LOGIN_FAILED"


def _fresh_auth():
    manual=_manual_ssid()
    if manual: _diag("PO_DIAG auth_source=PO_SSID"); return manual
    if _auto_auth_enabled():
        from pocket_auto_auth import get_fresh_ssid
        _diag("PO_DIAG auth_source=AUTO_BROWSER email_password_configured=yes")
        auth=get_fresh_ssid()
        if not auth: raise RuntimeError("Pocket Option automatic authentication returned no session")
        _diag("PO_DIAG auto_auth=CAPTURED format=AUTH_FRAME"); return auth
    raise RuntimeError("PO_LOGIN_EMAIL/PO_LOGIN_PASSWORD or PO_SSID are missing")


def _ssid_candidates(raw):
    seen=set(); queue=[(raw or "").strip()]; prefixes=("SSID:","ssid:","PO_SSID=","PO_SSID:","value:","Value:")
    while queue:
        value=queue.pop(0).strip()
        if not value or value in seen: continue
        seen.add(value); yield value
        for prefix in prefixes:
            if value.startswith(prefix): queue.append(value[len(prefix):].strip().strip('"\''))
        for _ in range(3):
            try: decoded=unquote(value).strip()
            except Exception: break
            if decoded==value: break
            queue.append(decoded); value=decoded
        if len(value)>=2 and value[0]=='"' and value[-1]=='"':
            try:
                decoded_json=json.loads(value)
                if isinstance(decoded_json,str): queue.append(decoded_json.strip())
            except Exception: pass
        idx=value.find('42["auth"')
        if idx>0: queue.append(value[idx:])


def _parse_ssid_local(candidate):
    raw=candidate.strip()
    if raw.startswith("42"): raw=raw[2:]
    try: payload=json.loads(raw)
    except json.JSONDecodeError as exc: raise ValueError("SSID is not valid JSON: %s" % exc) from exc
    if isinstance(payload,list) and len(payload)==2 and payload[0]=="auth": auth=payload[1]
    elif isinstance(payload,dict): auth=payload
    else: raise ValueError("SSID does not contain an auth payload")
    if not isinstance(auth,dict) or not str(auth.get("session") or "").strip(): raise ValueError("SSID auth payload is missing session")
    return auth,bool(auth.get("isDemo",0)),auth.get("uid")


def _validate_session_safe(raw):
    if not raw: raise ValueError("PO_SSID is empty")
    last=None
    for candidate in _ssid_candidates(raw):
        try:
            auth,is_demo,uid=_parse_ssid_local(candidate); session=str(auth.get("session") or "").strip(); digest=hashlib.sha256(session.encode()).hexdigest()[:12]
            _diag("PO_DIAG ssid_format=OK chars=%d demo=%s uid_present=%s session_sha256=%s" % (len(raw),int(bool(is_demo)),"yes" if uid is not None else "no",digest)); return candidate,bool(is_demo),uid
        except Exception as exc: last=exc
    uid_raw=(os.getenv("PO_UID") or "").strip(); compact="".join((raw or "").split())
    if uid_raw and compact and not compact.startswith(("http://","https://","socks5://","socks5h://")):
        try: uid_value=int(uid_raw)
        except ValueError: uid_value=uid_raw
        frame="42"+json.dumps(["auth",{"session":compact,"isDemo":1,"uid":uid_value,"platform":2,"isFastHistory":True,"isOptimized":True}],separators=(",",":"),ensure_ascii=False)
        digest=hashlib.sha256(compact.encode()).hexdigest()[:12]; _diag("PO_DIAG ssid_format=PLAIN_SESSION_WRAPPED chars=%d uid_present=yes session_sha256=%s" % (len(compact),digest)); return frame,True,uid_value
    raise ValueError("PO_SSID is not a valid Pocket Option auth payload: %s" % str(last or "invalid format")[:180])


def _connect_once(auth,use_proxy=False):
    from pocket_option_cmp import CmpPocketOption
    transport="SOCKS5" if use_proxy else "DIRECT"; _diag("PO_DIAG websocket=START transport=%s" % transport)
    client=CmpPocketOption(auth,use_proxy=use_proxy); ok,error=client.connect(timeout=45)
    if not ok or not client.check_connect():
        _diag("PO_DIAG websocket=FAILED transport=%s reason=%s" % (transport,str(error or "AUTH/CONNECT_FAILED")[:220])); client.disconnect_websocket(); raise RuntimeError(str(error or "Pocket Option auth/connect failed"))
    _diag("PO_DIAG websocket=OK connected=yes authenticated=yes transport=%s" % transport); setattr(client, "_pms_transport", "PROXY" if use_proxy else "DIRECT"); return client


def _connect_auto_browser():
    from pocket_auto_auth import get_fresh_ssid
    _diag("PO_DIAG auth_source=AUTO_BROWSER_FALLBACK"); auth=get_fresh_ssid()
    if not auth: raise RuntimeError("Pocket Option automatic authentication returned no session")
    transports=[True] if _proxy_configured_safe() else [False]
    last=None
    for use_proxy in transports:
        try: return _connect_once(auth,use_proxy=use_proxy)
        except Exception as exc: last=exc; _diag("PO_DIAG auto_auth=TRANSPORT_FAILED transport=%s reason=%s" % ("SOCKS5" if use_proxy else "DIRECT",str(exc)[:220]))
    raise RuntimeError(str(last) if last else "Pocket Option automatic authentication failed")


def _disconnect_stale_client(reason):
    global _client,_client_transport
    if _client is not None:
        _diag("PO_DIAG stale_client=REJECTED reason=%s transport=%s" % (reason,getattr(_client,"_pms_transport",None) or _client_transport or "unknown"))
        try: _client.disconnect_websocket()
        except Exception: pass
    _client=None
    _client_transport=None


def _connect():
    global _client,_client_transport,_last_auth_error,_last_auth_error_at
    proxy_required=_proxy_configured_safe()
    if _client is not None and _client.check_connect():
        actual=getattr(_client,"_pms_transport",None) or _client_transport
        if (not proxy_required) or actual=="PROXY":
            return _client
        _disconnect_stale_client("proxy_required_but_existing_client_not_proxy")
    now=time.time()
    if _last_auth_error and now-_last_auth_error_at<_AUTH_RETRY_COOLDOWN: raise RuntimeError(_last_auth_error)
    if not _lock.acquire(timeout=240.0): raise RuntimeError("Pocket Option authentication did not finish within 240s")
    try:
        proxy_required=_proxy_configured_safe()
        if _client is not None and _client.check_connect():
            actual=getattr(_client,"_pms_transport",None) or _client_transport
            if (not proxy_required) or actual=="PROXY":
                return _client
            _disconnect_stale_client("proxy_required_but_existing_client_not_proxy")
        if _last_auth_error and time.time()-_last_auth_error_at<_AUTH_RETRY_COOLDOWN: raise RuntimeError(_last_auth_error)
        if not configured():
            error="PO_LOGIN_EMAIL/PO_LOGIN_PASSWORD or PO_SSID are missing"; _last_auth_error,_last_auth_error_at=error,time.time(); raise RuntimeError(error)
        mode=_auth_mode(); _diag("PO_DIAG auth=START mode=%s" % mode)
        try:
            if mode=="AUTO_BROWSER":
                _client=_connect_auto_browser(); _client_transport=getattr(_client,"_pms_transport",None) or ("PROXY" if proxy_required else "DIRECT"); _last_auth_error=None; _last_auth_error_at=0.0; _diag("PO_DIAG auth=SUCCESS mode=AUTO_BROWSER transport=%s" % _client_transport); return _client
            auth=_fresh_auth()
            transports=[True] if proxy_required else [False]
            last=None
            for use_proxy in transports:
                try:
                    _client=_connect_once(auth,use_proxy=use_proxy); _client_transport="PROXY" if use_proxy else "DIRECT"; _last_auth_error=None; _last_auth_error_at=0.0; _diag("PO_DIAG auth=SUCCESS mode=%s transport=%s" % (mode,_client_transport)); return _client
                except Exception as exc:
                    last=exc; _diag("PO_DIAG auth=TRANSPORT_FAILED transport=%s reason=%s" % ("SOCKS5" if use_proxy else "DIRECT",str(exc)[:220]))
            if mode=="PO_SSID" and _auto_auth_enabled():
                _diag("PO_DIAG manual_auth=FAILED auto_auth=FALLBACK")
                try:
                    _client=_connect_auto_browser(); _client_transport=getattr(_client,"_pms_transport",None) or ("PROXY" if proxy_required else "DIRECT"); _last_auth_error=None; _last_auth_error_at=0.0; _diag("PO_DIAG auth=SUCCESS mode=AUTO_BROWSER_FALLBACK transport=%s" % _client_transport); return _client
                except Exception as auto_exc: last=auto_exc; _diag("PO_DIAG auto_auth=FALLBACK_FAILED reason=%s" % str(auto_exc)[:260])
            raise RuntimeError(str(last) if last else "Pocket Option authentication failed")
        except Exception as exc:
            _client=None; _client_transport=None
            kind=_classify_error(exc); message="Pocket Option %s: %s" % (kind,str(exc)[:300]); _last_auth_error,_last_auth_error_at=message,time.time(); _diag("PO_DIAG RESULT=%s reason=%s" % (kind,str(exc)[:300])); raise RuntimeError(message) from exc
    finally: _lock.release()


def client(): return _connect()


def on_tick(callback):
    api=client()
    if api is None or not api.check_connect(): raise RuntimeError("Pocket Option client is not connected")
    return api.on_tick(callback)


def subscribe(symbol):
    api=client()
    if api is None or not api.check_connect(): raise RuntimeError("Pocket Option client is not connected")
    return api.subscribe(symbol)


def _period_seconds(timeframe):
    tf=str(timeframe or "M5").strip().upper(); mapping={"S1":1,"S5":5,"S10":10,"S15":15,"S30":30,"M1":60,"M2":120,"M3":180,"M5":300,"M10":600,"M15":900,"M30":1800,"H1":3600,"H4":14400,"D1":86400}
    if tf not in mapping: raise ValueError("Unsupported Pocket Option timeframe: %s" % timeframe)
    return mapping[tf]


def _symbol_candidates(symbol):
    requested=str(symbol or "").strip()
    if not requested:return []
    out=[];seen=set()
    def add(value):
        value=str(value or "").strip()
        if value and value not in seen:seen.add(value);out.append(value)
    add(requested); upper=requested.upper(); is_otc=upper.endswith(" OTC") or upper.endswith("_OTC") or upper.endswith("OTC")
    base=requested
    if upper.endswith(" OTC") or upper.endswith("_OTC"): base=requested[:-4].strip()
    elif upper.endswith("OTC") and "/" not in requested: base=requested[:-3].strip(" _-")
    compact=base.replace("/","").replace(" ","")
    if compact:
        if is_otc:
            for suffix in ("_otc","_OTC","otc","OTC"): add(compact+suffix)
        add(compact)
    if is_otc and len(compact)==6: add(compact[:3]+"/"+compact[3:]+" OTC")
    elif not is_otc and len(compact)==6: add(compact[:3]+"/"+compact[3:])
    return out


def candles(symbol,timeframe="M5",count=120):
    api=_connect(); requested=str(symbol or "").strip(); candidates=_symbol_candidates(requested)
    if not candidates: raise ValueError("Pocket Option symbol is empty")
    period=_period_seconds(timeframe); _diag("PO_DIAG candles=requested=%s candidates=%s period=%s" % (requested,",".join(candidates[:6]),period))
    for candidate in candidates:
        rows=api.get_historical_candles(candidate,period,count_request=count)
        if rows:
            _diag("PO_FEED: source=POCKET_OPTION symbol=%s requested=%s timeframe=%s candles=%d" % (candidate,requested,timeframe,len(rows)))
            return rows,candidate
    raise RuntimeError("Pocket Option returned no candles for %s" % requested)


def get_assets(force=False):
    global _assets_cache
    now=time.time()
    if not force and _assets_cache["items"] is not None and now-_assets_cache["updated"]<_assets_ttl: return _assets_cache["items"]
    try:
        api=_connect(); assets=api.get_assets() or {}; _assets_cache={"items":assets,"updated":now}; _diag("PO_DIAG assets=REFRESH count=%d force=%s" % (len(assets),"yes" if force else "no")); return assets
    except Exception as exc:
        _diag("PO_DIAG assets=FAILED reason=%s" % str(exc)[:220]); return _assets_cache["items"] or []


def _run_worker_proxy_matrix():
    """Run the proxy matrix from the Gunicorn worker context, read-only."""
    if os.getenv("PO_PROXY_MATRIX_WORKER", "1") != "1":
        return
    try:
        from po_proxy_matrix import run as run_matrix
        run_matrix()
    except Exception as exc:
        _diag("PO_MATRIX worker_failed=%s" % str(exc)[:220])
