# Data sources and integration plan

## Current prototype
The Mini App reads public Binance spot candles for selected crypto assets. It computes RSI, SMA and EMA in the browser. It does not connect to Pocket Option, does not contain payout data, and does not produce trading instructions.

## Why OTC requires a separate source
Pocket Option OTC prices and payout percentages may be platform-specific and may not match public FX/crypto prices. Do not label third-party prices as Pocket Option data. A data integration should only be implemented after verifying a documented, authorized interface and its permitted use.

## Integration requirements
A future data adapter should expose normalized, timestamped records such as:
- assets: stable asset ID, display name, market type, availability timestamp
- candles: open time, open, high, low, close, volume (if available), source
- payouts: asset ID, percentage, observed timestamp, source (only if reliably provided)
- intervals: supported interval identifiers (only if confirmed by source)

## Reliability and security
- Show source and last-updated time in the UI.
- Mark data stale when it has not updated; never silently reuse old values as current.
- Validate server responses and rate limits.
- Never commit bot tokens, session IDs, cookies, passwords or API secrets.
- Do not place private credentials in frontend code. If an authorized provider requires a secret, keep it server-side.
- Do not assume a session ID is a documented API or a permanent connection method.
