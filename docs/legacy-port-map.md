# PMSignalPro → Pamires_bot: migration map

This document records source-code findings and defines a safe, incremental migration boundary. It is not a claim that the legacy service is currently running.

## Existing target architecture

- `index.html`: existing Mini App. Keep it; do not copy the legacy `web/` interface.
- `server.js`: Node.js API, Telegram polling, image-description endpoint, and proxy routes for market data.
- `pocket-worker/worker.py`: separate FastAPI worker. It has a read-only market-data interface, bearer-token check, connection state, asset listing, and candle retrieval.
- `lib/data-quality.js`: standalone Node.js validator for OHLC candles and snapshots.

## Legacy source map

| Legacy module | What source code indicates | Migration treatment |
|---|---|---|
| `pocket_feed.py` | Adapter around a lower-level feed client; normalizes candles and exposes assets/candles | Use for architecture reference only; do not duplicate a second transport in the Node API |
| `pocket_feed_cmp.py`, `pocket_option_cmp.py`, `pocket_option_socketio.py` | Connection/transport implementation and market-data retrieval | Keep separate from the target's existing worker until interface, dependency and authorization compatibility are reviewed |
| `pocket_auto_auth.py`, `pocket_auto_login*.py` | Browser-based session acquisition and authentication parsing | Do not copy session extraction or browser-login automation into this project |
| `data_quality.py` | OHLC shape, snapshot fields, timestamp normalization, freshness checks | Safe concept to port as neutral validation; the JS implementation already covers core candle/snapshot checks |
| `market_state.py`, `consistency.py` | Candle normalization and consistency handling used by legacy analysis | Review only for generic validation/normalization that can be tested independently of signal decisions |
| `technical_indicators.py`, `market_calculations.py`, `signal_engine.py`, `advanced_signals.py` | Numeric indicators and signal/decision logic | Not included in this migration tranche; this branch focuses on data integrity, not trading recommendations |
| `ai_analyzer.py` | Provider configuration and structured model response parsing for signal output | Do not transplant signal-generation prompts; target image endpoint remains educational and descriptive |
| `server.py`, user database and legacy `web/` | Flask API, user state/admin behavior, legacy interface | Do not replace the target's Node API, user handling, or existing Mini App |

## Target data contract

The existing worker returns candle records with `time`, `open`, `high`, `low`, `close`, and `volume`, plus `symbol`, `period`, `count`, and `source`. The Node API forwards the worker payload from `/api/market/candles`.

Before connecting validation to a route, add route-level tests for:
- malformed JSON or a non-object response;
- empty candle arrays;
- invalid OHLC ranges and non-finite numeric values;
- missing, duplicate, out-of-order, or stale timestamps;
- upstream worker errors and timeout behavior.

Never log or commit session IDs, cookies, passwords, Telegram bot tokens, worker bearer tokens, or provider API keys. Keep secrets server-side.

## Current status

- The target already has a worker and API proxy; do not build a parallel integration without a demonstrated need.
- The Node data-quality module is standalone and is not yet wired into `/api/market/candles`.
- The next safe step is to test the target's existing worker response contract, then decide where validation should run.
- A successful unit-test run proves code-level behavior only; it does not prove the worker can authenticate or reach the external provider.
