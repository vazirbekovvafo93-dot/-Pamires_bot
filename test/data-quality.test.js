const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeTimestamp, validateCandles } = require("../lib/data-quality");

test("normalizes epoch seconds, milliseconds, and microseconds", () => {
  assert.equal(normalizeTimestamp(1_700_000_000), 1_700_000_000_000);
  assert.equal(normalizeTimestamp(1_700_000_000_000), 1_700_000_000_000);
  assert.equal(normalizeTimestamp(1_700_000_000_000_000), 1_700_000_000_000);
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

test("reports missing timestamps as a warning", () => {
  const result = validateCandles([{ open: 10, high: 12, low: 9, close: 11 }]);
  assert.equal(result.ok, true);
  assert.equal(result.status, "warning");
  assert.match(result.warnings.join(" "), /timestamp/i);
});

test("rejects stale data when a freshness threshold is provided", () => {
  const result = validateCandles([{
    open: 10, high: 12, low: 9, close: 11,
    timestamp: Date.now() - 60_000
  }], { maxAgeMs: 1_000 });
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /stale/i);
});

test("warns when the latest timestamp is substantially in the future", () => {
  const result = validateCandles([{
    open: 10, high: 12, low: 9, close: 11,
    timestamp: Date.now() + 120_000
  }]);
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
