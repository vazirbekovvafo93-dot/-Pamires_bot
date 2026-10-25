# SIGNAL AI — Telegram Mini App

A dark, mobile-first Telegram Mini App for educational chart reading and market-data quality checks.

## Current scope

- Existing Mini App UI and local indicator summaries (RSI, moving averages, Bollinger bands and ATR).
- Descriptive chart/image explanations; the app should not generate trade instructions, forecasts, or buy/sell recommendations.
- Candle-response validation for malformed, duplicated, out-of-order, stale, or otherwise invalid data.
- No trade execution, account management, or user credential collection.

## Repository components

- index.html: static Telegram Mini App frontend.
- server.js: Node.js API, optional Telegram polling, descriptive image-analysis endpoint, and market-data proxy.
- lib/data-quality.js: market-response and candle validation.
- test/: automated validation tests.
- pocket-worker/: separate worker present in the repository. Its presence does not prove that a provider is reachable or that data is current.

## Deployment overview

The frontend can be published with GitHub Pages. The API is a separate Node.js service and must be deployed/configured separately for API-backed features to work. See DEPLOYMENT.md.

Do not assume that publishing the static frontend also deploys the API. The frontend's configured API URL must point to the intended API deployment.

## Verification status

CI checks JavaScript syntax and runs automated tests. Passing CI does not verify deployed runtime configuration, provider connectivity, live data freshness, or end-to-end Telegram behavior. Do not label upstream data as live unless freshness and source have been verified.

## Security

Never commit Telegram bot tokens, session IDs, cookies, passwords, worker bearer tokens, or API secrets. Do not put credentials in frontend JavaScript. Do not add browser-based session capture or unofficial login automation.
