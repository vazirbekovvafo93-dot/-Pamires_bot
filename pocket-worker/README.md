# SIGNAL AI — Pocket Option market-data worker

Separate, read-only service. It does not open orders or expose the SSID.

## Deploy as a separate Render web service

1. Create a **new** Web Service from this repository; do not replace the existing Node.js API.
2. Choose Docker runtime and set **Root Directory** to `pocket-worker`.
3. Add environment variables in Render:
   - `MARKET_API_TOKEN`: generate a long random secret (at least 32 bytes).
   - `POCKET_OPTION_SSID`: leave unset until the service is built and you are ready to connect.
4. Deploy. `GET /health` should show `ssid_required` until SSID is added.
5. Add `POCKET_OPTION_SSID` manually in Render Environment using the full auth payload captured from your own session. Never put it in GitHub, frontend code, chat, or logs.
6. Redeploy. Check `GET /health`; only proceed when connection is `connected`.

The protected endpoints require `Authorization: Bearer <MARKET_API_TOKEN>`:
- `GET /api/assets?otc_only=true`
- `GET /api/candles?symbol=EURUSD_otc&period=60&count=100`

Allowed candle periods are 5, 15, 60, 300, 900, and 3600 seconds. A period being accepted by this service does not guarantee Pocket Option will provide it for every asset. The third-party client may aggregate sub-minute bars from ticks.

## Important

- This uses an **unofficial** client and is not guaranteed to work with future Pocket Option protocol changes.
- The upstream client currently sets `requests.Session.verify = False`. Review/fix this before production use; this worker currently only needs the WebSocket path, but the dependency still contains that HTTP helper.
- No trade/order methods are called.
- Do not use your primary account until you have reviewed Pocket Option's rules and tested with a permitted session.
- Health endpoint intentionally omits errors and secrets; service logs must never include the SSID.
