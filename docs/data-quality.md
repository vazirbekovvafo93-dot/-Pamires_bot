# Data quality helper

This module provides structural validation for OHLC candle data. It does not calculate forecasts or recommend actions.

## API

```js
const { validateCandles } = require("./lib/data-quality");

const result = validateCandles(candles, {
  // Optional maximum allowed age in milliseconds.
  maxAgeMs: 3 * 60 * 1000
});

if (!result.ok) {
  console.error(result.errors);
}
if (result.warnings.length) {
  console.warn(result.warnings);
}
```

Each candle should contain finite numeric `open`, `high`, `low`, and `close` values. A timestamp can be provided as `timestamp`, `time`, or `t`; epoch seconds, milliseconds, and microseconds are normalized.

## Status meanings

- `ok`: no validation errors or warnings were detected.
- `warning`: no hard validation errors, but one or more caveats (for example, missing timestamps) prevent full verification.
- `invalid`: at least one validation error was detected.

If timestamps are absent, freshness cannot be confirmed. The caller must choose a freshness threshold appropriate to the data's documented timestamp format and sampling interval. This helper is intentionally standalone and is not wired into application routes.

## Tests

Run the unit tests with Node.js 20 or newer:

```bash
npm test
```

The tests cover timestamp normalization, valid and invalid OHLC values, missing timestamps, stale data, empty payloads, out-of-order timestamps, and invalid timestamps.
