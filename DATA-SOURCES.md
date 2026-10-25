# Data sources and validation

## What the repository currently implements

The frontend requests market assets and candles from the configured Node API. The Node API's /api/market/assets and /api/market/candles routes proxy to a separately configured worker. The repository also includes a worker implementation that uses an unofficial provider library. Source inspection does not establish that the worker is deployed, credentials are configured, the provider permits this use, or any returned data is live.

Do not present data as coming from a specific provider unless the source is verified at runtime. Do not label public-market candles as platform-specific OTC prices.

## Validation contract

The Node API validates the candle response before returning it. Checks include response shape, OHLC values, timestamp normalization, ordering, duplicate timestamps, stale/future timestamps, and empty or malformed payloads. Invalid payloads are rejected with an HTTP 502 response and a concise quality summary.

The freshness window is a defensive threshold (three requested periods), not a guarantee of live data. data_checked_at indicates when the API performed its validation; it is not the timestamp at which the provider generated the candle.

## Future adapters

Any replacement data adapter should use a documented, authorized source and return normalized records with:

- stable asset identifier and display name;
- market type and source name;
- candle open time, OHLC values, and volume if available;
- explicit source timestamp and supported interval identifiers.

Only include additional fields such as payouts if the source reliably documents them and their use is permitted. Do not infer missing fields or silently substitute another market's data.

## Reliability and security

- Show source and last-updated time in the UI when known.
- Mark stale data as stale; do not silently reuse it as current.
- Validate upstream responses and handle timeouts/rate limits.
- Keep credentials server-side and out of logs, commits, and frontend code.
- Do not use browser-based session capture or assume a session ID is a documented, stable API.
- Test validation with synthetic fixtures; tests should not require live provider access.
