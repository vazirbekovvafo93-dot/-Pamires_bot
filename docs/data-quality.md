# Data quality helper

This module validates read-only market-data payloads and the shape of upstream API responses. It does not calculate forecasts or recommend actions.

## Candle validation

```js
const { validateCandles } = require("./lib/data-quality");

const result = validateCandles(candles, {
  maxAgeMs: 3 * 60 * 1000,
  // Optional deterministic clock for tests:
  // nowMs: Date.now()
});
```

Each candle should contain finite numeric `open`, `high`, `low`, and `close` values. A timestamp can be provided as `timestamp`, `time`, or `t`; epoch seconds, milliseconds, and microseconds are normalized. The result includes `ok`, `status`, `errors`, `warnings`, `lastTimestamp`, and `ageMs`.

## Snapshot validation

```js
const { validateSnapshot } = require("./lib/data-quality");

const result = validateSnapshot({
  price: 1.2345,
  candle_time: Date.now()
});
```

A snapshot must be an object and include a finite numeric `price`. Known optional numeric fields are checked when supplied; missing optional fields are reported as warnings. This validates payload shape only and does not generate or evaluate a signal.

## Status meanings

- `ok`: no validation errors or warnings were detected.
- `warning`: no hard validation errors, but one or more caveats prevent full verification.
- `invalid`: at least one validation error was detected.

The helper is used by `GET /api/market/candles` to validate the worker payload before returning it. Invalid candles produce HTTP 502 with a `data_quality` error summary. Valid responses include `data_quality.status`, warnings, and `data_checked_at`. The route uses a freshness window of three requested periods; this is a defensive threshold, not a guarantee that the upstream source is live.

## Tests

Run the unit tests with Node.js 20 or newer:

```bash
npm test
```

Tests cover timestamp normalization, OHLC consistency, missing timestamps, stale/future data, empty payloads, ordering, duplicate timestamps, malformed candle rows, and snapshot validation.

## Upstream response contract

`validateMarketResponse(payload, options)` checks that the response is an object with a `candles` array, then applies candle validation. The Node route `GET /api/market/candles` uses this helper before forwarding worker data. Invalid responses return HTTP 502 with a short quality summary; valid responses include the validation status and check timestamp.
