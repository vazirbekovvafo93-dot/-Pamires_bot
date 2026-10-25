# SIGNAL AI — Pocket Option market-data worker

Existing Render service: `signal-ai-pocket-worker`. This worker is read-only and only exposes authenticated asset/candle endpoints. It does not place trades.

## Render environment

Set these variables on the existing worker service:

- `PO_SSID` or the legacy key `POCKET_OPTION_SSID`: the complete Pocket Option auth payload from your own current session. Keep it only in Render Environment.
- `MARKET_API_TOKEN`: a long random secret. Use the same value on the Node API service, where it is stored server-side.

After saving the variables, let Render redeploy. Check `GET /health`; the connection should read `connected`. If it reports `connection_failed`, recapture the SSID rather than posting it in chat or committing it to GitHub.

## Endpoints

The existing Node API proxies to this worker, using a server-side Bearer token:

- `GET /api/assets?otc_only=true`
- `GET /api/candles?symbol=EURUSD_otc&period=60&count=100`

Allowed candle periods: 5, 15, 60, 300, 900, and 3600 seconds. Support depends on what Pocket Option actually returns for a particular asset/timeframe.

## Limitations

This integration uses an unofficial WebSocket protocol implementation. Pocket Option can change the protocol, expire session IDs, or restrict access; successful deployment alone does not prove authentication or data retrieval. The implementation is kept read-only and does not expose order/trade methods. The session ID is a sensitive account credential—never place it in frontend code, GitHub, chat, or logs.
