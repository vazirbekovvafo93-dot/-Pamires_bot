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
  res.json({ service: "SIGNAL AI API", status: "ok", mode: "setup" });
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

// Upload validation is ready. Actual AI interpretation requires a server-side AI API key.
app.post("/api/analyze-image", upload.single("image"), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "Attach an image using the 'image' field." });
  if (!process.env.OPENAI_API_KEY) {
    return res.status(503).json({
      error: "AI analysis is not configured yet.",
      message: "Add OPENAI_API_KEY in Render Environment Variables to enable image analysis."
    });
  }

  return res.status(501).json({
    error: "AI provider integration is not enabled in this initial backend.",
    message: "Image upload was validated, but no image was sent to an AI model."
  });
});

app.use((err, _req, res, _next) => {
  if (err instanceof multer.MulterError && err.code === "LIMIT_FILE_SIZE") {
    return res.status(413).json({ error: "Image must be 8 MB or smaller." });
  }
  return res.status(400).json({ error: err.message || "Bad request." });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`SIGNAL AI API listening on port ${port}`);
});
