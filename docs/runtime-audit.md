# Runtime and frontend contract audit

## Findings from source inspection

The current branch has multiple overlapping descriptions of the app's behavior. Treat this as a source-code audit, not proof of what is currently deployed.

1. `index.html` embeds the API base URL `https://signal-ai-api-dbgp.onrender.com`.
2. The frontend calls `/api/market/assets` and `/api/market/candles`; these are the Node server routes that proxy to the separately configured worker.
3. The frontend markup still contains trading-oriented language such as “Получить сигнал”, “Сигнал”, “Точность”, “Направление”, and a “Pocket Option ID” placeholder.
4. `README.md` and `DEPLOYMENT.md` describe the current product as a public-crypto educational demo without binary-options signals and say that the current site is static.
5. `server.js` contains backend routes for the worker and an image-description endpoint. Therefore the repository documentation does not fully describe the source currently present on the audited branch.
6. `pocket-worker/worker.py` uses an unofficial WebSocket library and expects `POCKET_OPTION_SSID` and `MARKET_API_TOKEN`. Source inspection alone cannot confirm that these values are configured or that a remote service is connected.

## What has and has not been verified

- Verified from repository source: route names, environment-variable names, response shape, validation code, and the frontend's configured API base URL.
- Verified by CI: Node syntax checks and unit tests pass on the tested branch commit.
- Not verified: current deployment version, actual runtime environment variables, external-provider connectivity, live data freshness, or end-to-end behavior from the deployed Mini App.
- No secrets were read, printed, or added to the repository.

## Recommended safe next steps

- Reconcile the README/deployment documentation with the actual code and the intended educational product scope.
- Keep the existing Mini App and avoid introducing a second authentication flow or copying legacy user/profile code.
- Test data validation against synthetic fixtures and documented public-data sources.
- Do not present public-market candles as Pocket Option OTC data, and do not claim a live provider connection without a runtime health check from an authorized environment.
