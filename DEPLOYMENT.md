# Deployment guide

This repository contains two separately deployed parts: the static Mini App frontend and a Node.js API. Publishing the frontend alone does not deploy the API.

## 1. Publish the frontend with GitHub Pages

1. Open the repository on GitHub.
2. Go to Settings → Pages.
3. Under Build and deployment → Source, choose GitHub Actions if the repository's Pages workflow is enabled.
4. Open Actions and confirm the deployment workflow succeeds.
5. The expected Pages address is https://vazirbekovvafo93-dot.github.io/-Pamires_bot/.

If the workflow fails, inspect the failed run logs. Do not add secrets to index.html.

## 2. API deployment and configuration

server.js is a separate Node.js service. The frontend currently has a configured API base URL, so API-backed features depend on that service being available and on the URL matching the intended deployment.

The server supports a basic /health endpoint. That endpoint confirms only that the Node process responds; it does not verify market-data provider connectivity or freshness.

The repository also contains market-data proxy routes and a worker. Do not treat those routes as proof of a working provider connection. Do not enable or rely on unofficial session-based provider access; use only a documented, authorized data interface. No provider connection is asserted by this guide.

The image-description endpoint requires a server-side GEMINI_API_KEY. Optional Telegram polling requires a server-side TELEGRAM_BOT_TOKEN. Store such values only in the hosting provider's environment settings, never in frontend code or Git.

## 3. Connect Telegram

1. Open https://t.me/BotFather.
2. Use /setmenubutton, select the bot, and set a button title such as Open SIGNAL AI.
3. Enter the HTTPS Pages URL above.
4. Open the bot in Telegram and tap the menu button.

Only configure Telegram after the static site is published and accessible over HTTPS.

## 4. Data limitations

The API route validates response structure and candle quality, but this is not proof that upstream data is authoritative, current, or appropriate for a particular market. Public-market prices may differ from platform-specific or OTC quotes. The UI should show the source and data age when available and should not produce trading instructions.

## 5. Security checklist

- Never commit bot tokens, session IDs, cookies, passwords, worker bearer tokens, or API secrets.
- Never place private credentials in frontend JavaScript.
- Do not implement browser session extraction or unofficial login automation.
- Restrict API CORS origins in production with ALLOWED_ORIGIN when feasible.
- Confirm runtime behavior in the deployed environment; CI alone does not verify deployment.
