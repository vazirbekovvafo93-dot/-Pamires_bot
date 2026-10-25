/**
 * Neutral validation for read-only market-data payloads.
 * This module does not generate forecasts or trading recommendations.
 */

function normalizeTimestamp(value) {
  if (value === null || value === undefined || value === "") return null;
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
    if (!candle || typeof candle !== "object" || Array.isArray(candle)) {
      errors.push(`Candle ${index}: expected an object`);
      return;
    }

    const rawValues = ["open", "high", "low", "close"].map((key) => candle[key]);
    const values = rawValues.map((value) =>
      value === null || value === undefined || value === "" ? NaN : Number(value)
    );
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
    } else if (previousTimestamp !== null && timestamp === previousTimestamp) {
      errors.push(`Candle ${index}: duplicate timestamp`);
    }
    previousTimestamp = timestamp;
    lastTimestamp = timestamp;
  });

  if (timestampsSeen === 0) {
    warnings.push("Freshness cannot be confirmed because timestamps are missing or invalid");
  }

  const nowMs = Number.isFinite(options.nowMs) ? options.nowMs : Date.now();
  const ageMs = lastTimestamp === null ? null : nowMs - lastTimestamp;
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

const SNAPSHOT_NUMERIC_FIELDS = [
  "price", "ema20", "ema50", "rsi", "atr", "adx",
  "di_plus", "di_minus", "macd", "macd_signal", "macd_hist"
];

function isFiniteNumber(value) {
  if (typeof value === "boolean" || value === null || value === undefined || value === "") {
    return false;
  }
  return Number.isFinite(Number(value));
}

/**
 * Validate a read-only snapshot's data shape. Missing optional fields are
 * warnings; malformed supplied numeric fields are errors.
 */
function validateSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    return { ok: false, errors: ["Snapshot must be an object"], warnings: [] };
  }

  const errors = [];
  const warnings = [];
  if (!isFiniteNumber(snapshot.price)) errors.push("Price is missing or invalid");

  for (const field of SNAPSHOT_NUMERIC_FIELDS) {
    if (field in snapshot && snapshot[field] !== null && !isFiniteNumber(snapshot[field])) {
      errors.push(`${field} must be a finite number`);
    }
  }

  for (const field of ["ema20", "ema50", "rsi", "atr", "adx"]) {
    if (snapshot[field] === null || snapshot[field] === undefined || snapshot[field] === "") {
      warnings.push(`${field} is missing`);
    }
  }
  if (snapshot.candle_time === null || snapshot.candle_time === undefined || snapshot.candle_time === "") {
    warnings.push("Candle timestamp is missing");
  }

  return { ok: errors.length === 0, errors, warnings };
}


/**
 * Validate the shape of an upstream market API response before forwarding it.
 * This checks data integrity only; it does not interpret or recommend actions.
 */
function validateMarketResponse(payload, options = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return {
      ok: false,
      status: "invalid",
      errors: ["Market response must be an object"],
      warnings: []
    };
  }
  if (!Array.isArray(payload.candles)) {
    return {
      ok: false,
      status: "invalid",
      errors: ["Market response candles must be an array"],
      warnings: []
    };
  }
  const result = validateCandles(payload.candles, options);
  return { ...result };
}

module.exports = { normalizeTimestamp, validateCandles, validateSnapshot, validateMarketResponse };
