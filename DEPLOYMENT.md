# Deployment guide (iPhone-friendly)

## 1. Publish with GitHub Pages
1. Open this repository on GitHub.
2. Go to **Settings → Pages**.
3. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. Open the **Actions** tab and wait for **Deploy Telegram Mini App to GitHub Pages** to finish.
5. The public address will be:
   `https://vazirbekovvafo93-dot.github.io/-Pamires_bot/`

If the workflow fails, open the failed run and inspect its log. Do not add secrets to `index.html`.

## 2. Connect Telegram
1. Open [@BotFather](https://t.me/BotFather).
2. Use `/setmenubutton`, select your bot, enter a button title such as `Open SIGNAL AI`.
3. Paste the published HTTPS URL above.
4. Open your bot in Telegram and tap the menu button.

## 3. Current data limitations
The current prototype uses Binance's public spot-candle endpoint for BTC, ETH, BNB, SOL and XRP against USDT. These are **not Pocket Option OTC quotes** and payout percentages are not included. The app labels its values as public market data and does not generate trading instructions or place trades.

## 4. Security
- Never commit Telegram bot tokens, Pocket Option session IDs, cookies, passwords, or API secrets.
- Do not place secrets in frontend JavaScript; every visitor can inspect it.
- The current site is static and does not need a bot token or backend.
- A direct Pocket Option integration should only use an official or otherwise authorized data interface. Do not rely on a session ID as a guaranteed API.
