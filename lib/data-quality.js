/**
 * Neutral validation for OHLC market-data payloads.
 * This module does not generate forecasts or trading recommendations.
 */

function normalizeTimestamp(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  // Accept epoch seconds, milliseconds, or microseconds.
  if (n < 1e11) return n * 1000;
  if (n > 1e14) return n / 1000;
  return n;
}

function validateCandles(candles, options = {}) {
  const errors = [];
  const warnings = [];
  const maxAgeMs = Number.isFinite(options.maxAgeMs) && options.maxAgeMs >= 0
    ? options.maxAgeMs
    : null;

  if (!Array.isArray(candles) || candles.length === 0) {
    return {
      ok: false,
      status: "invalid",
      errors: ["No candle data supplied"],
      warnings,
      lastTimestamp: null,
      ageMs: null
    };
  }

  let lastTimestamp = null;
  let previousTimestamp = null;
  let timestampsSeen = 0;

  candles.forEach((candle, index) => {
    if (!candle || typeof candle !== "object") {
      errors.push(`Candle ${index}: expected an object`);
      return;
    }

    const values = ["open", "high", "low", "close"].map((key) => Number(candle[key]));
    if (!values.every(Number.isFinite)) {
      errors.push(`Candle ${index}: OHLC values must be finite numbers`);
      return;
    }

    const [open, high, low, close] = values;
    if (low > high || high < Math.max(open, close) || low > Math.min(open, close)) {
      errors.push(`Candle ${index}: inconsistent OHLC range`);
    }

    const rawTime = candle.timestamp ?? candle.time ?? candle.t;
    if (rawTime === undefined || rawTime === null || rawTime === "") {
      warnings.push(`Candle ${index}: timestamp missing`);
      return;
    }

    const timestamp = normalizeTimestamp(rawTime);
    if (timestamp === null) {
      errors.push(`Candle ${index}: invalid timestamp`);
      return;
    }

    timestampsSeen += 1;
    if (previousTimestamp !== null && timestamp < previousTimestamp) {
      errors.push(`Candle ${index}: timestamps are not in ascending order`);
    }
    previousTimestamp = timestamp;
    lastTimestamp = timestamp;
  });

  if (timestampsSeen === 0) {
    warnings.push("Freshness cannot be confirmed because timestamps are missing");
  }

  const ageMs = lastTimestamp === null ? null : Date.now() - lastTimestamp;
  if (ageMs !== null && ageMs < -60_000) {
    warnings.push("Latest candle timestamp is in the future");
  } else if (maxAgeMs !== null && ageMs !== null && ageMs > maxAgeMs) {
    errors.push("Candle data is stale");
  }

  return {
    ok: errors.length === 0,
    status: errors.length ? "invalid" : warnings.length ? "warning" : "ok",
    errors,
    warnings,
    lastTimestamp,
    ageMs
  };
}

module.exports = { normalizeTimestamp, validateCandles };
