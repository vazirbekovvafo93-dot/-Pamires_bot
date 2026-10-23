# SIGNAL AI — Telegram Mini App

A dark, mobile-first Telegram Mini App for educational market-chart analysis.

## Current scope
- Mobile dashboard UI
- Public market-data demo (crypto candles)
- RSI and moving-average indicators
- No trade execution and no binary-options signals

## Deploy
Publish the repository as a static site using GitHub Pages or Cloudflare Pages. Configure the HTTPS URL as the Telegram Mini App URL in BotFather.

## Security
Never commit Telegram bot tokens, session IDs, cookies, API secrets, or other credentials. A static frontend must not contain secrets.

## Pocket Option SSID market-data worker (read-only)

A separate Docker service lives in `pocket-worker/`. It is deliberately isolated from the existing Node API and does not place the SSID in the repository.

### Setup sequence

1. Create a **new** Render Web Service from this repo using Docker and set Root Directory to `pocket-worker`. Keep the existing Node API service unchanged.
2. Add `MARKET_API_TOKEN` to both the new worker and the existing Node API. Use the same long random value (at least 32 bytes).
3. Initially leave `POCKET_OPTION_SSID` unset. Confirm the worker builds and `/health` reports `ssid_required`.
4. Add `POCKET_WORKER_URL` to the Node API, set to the new worker's HTTPS base URL.
5. Only when the worker is ready, manually add `POCKET_OPTION_SSID` to the worker's Render Environment using the full auth payload. Do not paste it into GitHub, code, or chat.
6. Redeploy the worker and check `/health` reports `connected`.

The Node API exposes `GET /api/market/assets` and `GET /api/market/candles?symbol=EURUSD_otc&period=60&count=100`. These are backend endpoints; the current frontend has not yet been wired to display Pocket Option data or calculate indicators from this feed.

**Status:** integration scaffold only; not yet tested against a real Pocket Option session. The client is unofficial, and supported symbols/periods must be confirmed at runtime. Review the upstream client's TLS-verification behavior and Pocket Option's terms before production use.

