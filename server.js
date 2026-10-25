const express = require("express");
const cors = require("cors");
const multer = require("multer");

const app = express();
const port = process.env.PORT || 3000;
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 8 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    if (/^image\/(png|jpeg|webp)$/.test(file.mimetype)) return cb(null, true);
    cb(new Error("Upload a PNG, JPEG, or WebP image."));
  }
});

app.use(cors({
  origin: (process.env.ALLOWED_ORIGIN || "*").split(",").map(v => v.trim()),
  methods: ["GET", "POST"]
}));
app.use(express.json({ limit: "1mb" }));

app.get("/", (_req, res) => {
  res.json({ service: "SIGNAL AI API", status: "ok", mode: process.env.TELEGRAM_BOT_TOKEN ? "telegram-enabled" : "setup" });
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

const MARKET_PERIODS = new Set([5, 15, 60, 300, 900, 3600]);

async function proxyMarketRequest(path, query = "") {
  const base = (process.env.POCKET_WORKER_URL || "").replace(/\/$/, "");
  const token = process.env.MARKET_API_TOKEN || "";
  if (!base || !token) {
    const error = new Error("Pocket Option market worker is not configured.");
    error.status = 503;
    throw error;
  }
  const response = await fetch(`${base}${path}${query}`, {
    headers: { authorization: `Bearer ${token}`, accept: "application/json" },
    signal: AbortSignal.timeout(12000)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || "Market data service unavailable.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

app.get("/api/market/assets", async (_req, res) => {
  try {
    const result = await proxyMarketRequest("/api/assets", "?otc_only=true");
    res.json(result);
  } catch (error) {
    res.status(error.status || 502).json({ error: error.message || "Market data service unavailable." });
  }
});

app.get("/api/market/candles", async (req, res) => {
  const symbol = typeof req.query.symbol === "string" ? req.query.symbol : "";
  const period = Number(req.query.period || 60);
  const count = Number(req.query.count || 100);
  if (!/^[A-Za-z0-9_.-]{2,80}$/.test(symbol)) {
    return res.status(400).json({ error: "Invalid market symbol." });
  }
  if (!MARKET_PERIODS.has(period)) {
    return res.status(400).json({ error: "Unsupported period. Use 5, 15, 60, 300, 900 or 3600 seconds." });
  }
  if (!Number.isInteger(count) || count < 1 || count > 500) {
    return res.status(400).json({ error: "count must be an integer from 1 to 500." });
  }
  try {
    const query = `?symbol=${encodeURIComponent(symbol)}&period=${period}&count=${count}`;
    const result = await proxyMarketRequest("/api/candles", query);
    res.json(result);
  } catch (error) {
    res.status(error.status || 502).json({ error: error.message || "Market data service unavailable." });
  }
});

app.post("/api/analyze-image", upload.single("image"), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "Прикрепи изображение в поле image." });
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return res.status(503).json({ error: "Gemini пока не настроен. Добавь GEMINI_API_KEY в Environment на Render." });
  }

  try {
    const prompt = [
      "Ты помощник для финансового обучения. Опиши только то, что действительно видно на изображении графика.",
      "Ответь на русском простыми словами, короткими разделами: 1) что изображено и какой актив/таймфрейм можно прочитать; 2) видимые свечи и возможная структура графика; 3) индикаторы и их значения, только если они читаемы; 4) ограничения изображения и что невозможно определить.",
      "Не давай торговых сигналов, прогнозов направления, рекомендаций купить/продать, времени входа, ставок или обещаний доходности. Не выдумывай цифры и индикаторы. Если подписи или изображение неразборчивы, прямо сообщи об этом. Это образовательное описание, не финансовая рекомендация."
    ].join("\n");

    const response = await fetch("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-goog-api-key": apiKey
      },
      body: JSON.stringify({
        contents: [{
          role: "user",
          parts: [
            { text: prompt },
            { inline_data: { mime_type: req.file.mimetype, data: req.file.buffer.toString("base64") } }
          ]
        }],
        generationConfig: { temperature: 0.2, maxOutputTokens: 500 }
      }),
      signal: AbortSignal.timeout(20000)
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      console.error("Gemini API error:", response.status, payload?.error?.message || "unknown error");
      return res.status(502).json({ error: response.status === 429 || response.status === 503
        ? "AI сейчас перегружен. Подожди 10–20 секунд и попробуй снова."
        : "Gemini не смог обработать изображение. Проверь API-ключ и попробуй снова." });
    }
    const text = (payload.candidates?.[0]?.content?.parts || [])
      .map(part => part.text || "")
      .join("\n")
      .trim();
    if (!text) return res.status(502).json({ error: "AI не вернул текст. Попробуй загрузить более чёткое изображение." });
    return res.json({ analysis: text, model: "gemini-2.5-flash", educational: true });
  } catch (error) {
    console.error("Image analysis failed:", error.message);
    return res.status(504).json({ error: "Не удалось завершить анализ. Проверь соединение и попробуй ещё раз." });
  }
});

app.use((err, _req, res, _next) => {
  if (err instanceof multer.MulterError && err.code === "LIMIT_FILE_SIZE") {
    return res.status(413).json({ error: "Image must be 8 MB or smaller." });
  }
  return res.status(400).json({ error: err.message || "Bad request." });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`SIGNAL AI API listening on port ${port}`);
  startTelegramBot();
});

async function startTelegramBot() {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    console.log("TELEGRAM_BOT_TOKEN is missing; Telegram bot polling is disabled.");
    return;
  }

  const api = `https://api.telegram.org/bot${token}`;
  let offset = 0;
  const appUrl = "https://vazirbekovvafo93-dot.github.io/-Pamires_bot/";

  async function sendMessage(chatId, text) {
    const response = await fetch(`${api}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true })
    });
    if (!response.ok) console.error("Telegram sendMessage failed:", response.status);
  }

  let pollingStopped = false;
  let pollingStarted = false;

  async function poll() {
    if (pollingStopped) return;
    try {
      const response = await fetch(`${api}/getUpdates?timeout=25&offset=${offset}`);
      if (response.status === 409) {
        pollingStopped = true;
        console.error("Telegram polling stopped: getUpdates returned 409; another poller or webhook is active.");
        return;
      }
      if (!response.ok) throw new Error(`Telegram getUpdates returned ${response.status}`);
      const payload = await response.json();
      if (payload.error_code === 409) {
        pollingStopped = true;
        console.error("Telegram polling stopped: getUpdates returned 409; another poller or webhook is active.");
        return;
      }
      if (!payload.ok) throw new Error("Telegram API did not return ok");
      for (const update of payload.result || []) {
        offset = update.update_id + 1;
        const message = update.message;
        if (!message?.chat?.id || typeof message.text !== "string") continue;
        const text = message.text.trim();
        if (text === "/start" || text.startsWith("/start ")) {
          await sendMessage(message.chat.id,
            "Привет! Это SIGNAL AI — учебное приложение для знакомства с графиками и техническими индикаторами.\n\nОткрой приложение кнопкой SIGNAL AI внизу чата или по ссылке:\n" + appUrl + "\n\nДанные могут отличаться от OTC-котировок. Это не финансовая рекомендация.");
        } else if (text === "/help") {
          await sendMessage(message.chat.id, "Нажми кнопку SIGNAL AI внизу чата, чтобы открыть приложение. Я могу помочь с общими вопросами о графиках и индикаторах.");
        } else {
          await sendMessage(message.chat.id, "Я пока понимаю команды /start и /help. Нажми кнопку SIGNAL AI внизу чата, чтобы открыть приложение.");
        }
      }
    } catch (error) {
      console.error("Telegram polling error:", error.message);
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
    if (!pollingStopped) setTimeout(poll, 100);
  }

  try {
    const webhookResponse = await fetch(`${api}/deleteWebhook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ drop_pending_updates: false })
    });
    if (!webhookResponse.ok) throw new Error("Could not configure Telegram polling");
    console.log("Telegram bot polling started.");
    poll();
  } catch (error) {
    console.error("Telegram bot setup failed:", error.message);
  }
}
