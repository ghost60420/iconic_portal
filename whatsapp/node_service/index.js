require("dotenv").config();
const express = require("express");
const fs = require("fs");
const axios = require("axios");
const qrcode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");

const HOST = process.env.WHATSAPP_SERVICE_HOST || "127.0.0.1";
const PORT = parseInt(process.env.WHATSAPP_SERVICE_PORT || "3127", 10);
const WEBHOOK_URL = process.env.WHATSAPP_WEBHOOK_URL || "http://127.0.0.1:8000/whatsapp/webhook/";
const WEBHOOK_SECRET = process.env.WHATSAPP_WEBHOOK_SECRET || "";
const SERVICE_SECRET = process.env.WHATSAPP_SERVICE_SECRET || "";
const SESSION_PATH = process.env.WHATSAPP_SESSION_PATH || "./session";

let latestQr = null;
let status = "disconnected";

if (!fs.existsSync(SESSION_PATH)) {
  fs.mkdirSync(SESSION_PATH, { recursive: true });
}

const app = express();
app.use(express.json({ limit: "1mb" }));

function auth(req, res, next) {
  if (SERVICE_SECRET && req.header("X-WhatsApp-Secret") !== SERVICE_SECRET) {
    return res.status(403).json({ ok: false, error: "forbidden" });
  }
  return next();
}

async function postEvent(payload) {
  if (!WEBHOOK_URL) return;
  try {
    await axios.post(WEBHOOK_URL, payload, {
      headers: WEBHOOK_SECRET ? { "X-WhatsApp-Secret": WEBHOOK_SECRET } : {},
      timeout: 5000,
    });
  } catch (err) {
    // swallow webhook errors to avoid crashing the service
  }
}

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: SESSION_PATH }),
  puppeteer: {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

client.on("qr", (qr) => {
  latestQr = qr;
  status = "qr_required";
  postEvent({ event: "qr_required" });
});

client.on("ready", () => {
  status = "connected";
  latestQr = null;
  postEvent({ event: "session_connected" });
});

client.on("authenticated", () => {
  status = "connected";
});

client.on("auth_failure", (msg) => {
  status = "error";
  postEvent({ event: "auth_failure", reason: msg || "auth failure" });
});

client.on("disconnected", (reason) => {
  status = "disconnected";
  postEvent({ event: "session_disconnected", reason: reason || "disconnected" });
});

client.on("message", async (msg) => {
  if (msg.fromMe) return;
  const chatId = msg.from;
  const phone = chatId.replace(/@c\.us|@g\.us/g, "");
  let contactName = "";
  try {
    const contact = await msg.getContact();
    contactName = contact.pushname || contact.name || "";
  } catch (err) {
    contactName = "";
  }

  postEvent({
    event: "message",
    chat_id: chatId,
    from: phone,
    body: msg.body || "",
    message_id: msg.id ? msg.id.id : "",
    contact_name: contactName,
  });
});

client.initialize();

app.get("/status", auth, (req, res) => {
  res.json({ ok: true, status, has_qr: !!latestQr });
});

app.get("/qr", auth, async (req, res) => {
  if (!latestQr) {
    return res.json({ ok: false, error: "no_qr" });
  }
  const dataUrl = await qrcode.toDataURL(latestQr);
  return res.json({ ok: true, qr: dataUrl });
});

app.post("/send", auth, async (req, res) => {
  const message = (req.body.message || req.body.text || "").trim();
  if (!message) {
    return res.status(400).json({ ok: false, error: "message_required" });
  }

  let chatId = req.body.chat_id || "";
  if (!chatId) {
    const phone = (req.body.phone || "").replace(/\D/g, "");
    if (!phone) {
      return res.status(400).json({ ok: false, error: "phone_required" });
    }
    chatId = `${phone}@c.us`;
  }

  try {
    const result = await client.sendMessage(chatId, message);
    const messageId = result && result.id ? (result.id.id || result.id._serialized) : "";
    return res.json({ ok: true, message_id: messageId });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message || "send_failed" });
  }
});

app.post("/logout", auth, async (req, res) => {
  try {
    await client.logout();
    latestQr = null;
    status = "qr_required";
    return res.json({ ok: true });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message || "logout_failed" });
  }
});

app.listen(PORT, HOST, () => {
  // eslint-disable-next-line no-console
  console.log(`WhatsApp web service listening on http://${HOST}:${PORT}`);
});
