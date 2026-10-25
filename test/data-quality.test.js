const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeTimestamp, validateCandles, validateSnapshot } = require("../lib/data-quality");

test("normalizes epoch seconds, milliseconds, and microseconds", () => {
  assert.equal(normalizeTimestamp(1_700_000_000), 1_700_000_000_000);
  assert.equal(normalizeTimestamp(1_700_000_000_000), 1_700_000_000_000);
  assert.equal(normalizeTimestamp(1_700_000_000_000_000), 1_700_000_000_000);
  assert.equal(normalizeTimestamp(""), null);
  assert.equal(normalizeTimestamp(null), null);
});

test("accepts a valid candle with a recent timestamp", () => {
  const result = validateCandles([{
    open: 10, high: 12, low: 9, close: 11,
    timestamp: Date.now()
  }]);
  assert.equal(result.ok, true);
  assert.equal(result.status, "ok");
});

test("rejects inconsistent OHLC values", () => {
  const result = validateCandles([{
    open: 10, high: 9, low: 8, close: 11,
    timestamp: Date.now()
  }]);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /inconsistent OHLC/i);
});

test("rejects null or empty OHLC values instead of coercing them to zero", () => {
  for (const missingValue of [null, ""]) {
    const result = validateCandles([{
      open: 10, high: 12, low: 9, close: missingValue,
      timestamp: Date.now()
    }]);
    assert.equal(result.ok, false);
    assert.match(result.errors.join(" "), /finite numbers/i);
  }
});

test("reports missing timestamps as a warning", () => {
  const result = validateCandles([{ open: 10, high: 12, low: 9, close: 11 }]);
  assert.equal(result.ok, true);
  assert.equal(result.status, "warning");
  assert.match(result.warnings.join(" "), /timestamp/i);
});

test("rejects stale data when a freshness threshold is provided", () => {
  const nowMs = 1_700_000_060_000;
  const result = validateCandles([{
    open: 10, high: 12, low: 9, close: 11,
    timestamp: 1_700_000_000_000
  }], { maxAgeMs: 1_000, nowMs });
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /stale/i);
});

test("warns when the latest timestamp is substantially in the future", () => {
  const nowMs = 1_700_000_000_000;
  const result = validateCandles([{
    open: 10, high: 12, low: 9, close: 11,
    timestamp: nowMs + 120_000
  }], { nowMs });
  assert.equal(result.ok, true);
  assert.equal(result.status, "warning");
  assert.match(result.warnings.join(" "), /future/i);
});

test("rejects an empty payload", () => {
  const result = validateCandles([]);
  assert.equal(result.ok, false);
  assert.equal(result.status, "invalid");
});

test("rejects timestamps that are out of order", () => {
  const now = Date.now();
  const result = validateCandles([
    { open: 10, high: 12, low: 9, close: 11, timestamp: now },
    { open: 11, high: 13, low: 10, close: 12, timestamp: now - 1000 }
  ]);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /ascending order/i);
});

test("rejects invalid timestamps", () => {
  const result = validateCandles([{
    open: 10, high: 12, low: 9, close: 11, timestamp: "not-a-time"
  }]);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /invalid timestamp/i);
});

test("validates a snapshot and reports absent optional fields as warnings", () => {
  const result = validateSnapshot({ price: "1.2345" });
  assert.equal(result.ok, true);
  assert.equal(result.errors.length, 0);
  assert.match(result.warnings.join(" "), /ema20 is missing/);
});

test("rejects invalid supplied snapshot numbers and booleans", () => {
  const result = validateSnapshot({ price: true, rsi: "not-a-number" });
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /Price is missing or invalid/);
  assert.match(result.errors.join(" "), /rsi must be a finite number/);
});

test("rejects non-object snapshots", () => {
  const result = validateSnapshot([]);
  assert.equal(result.ok, false);
});
