const express = require('express');
const qrcode = require('qrcode');
const { Client, LocalAuth } = require('whatsapp-web.js');

const app = express();
app.use(express.json({ limit: '2mb' }));

const PORT = parseInt(process.env.WA_WEB_PORT || '3340', 10);
const API_KEY = (process.env.WA_WEB_API_KEY || '').trim();
const INGEST_URL = (process.env.WA_WEB_INGEST_URL || '').trim();
const INGEST_TOKEN = (process.env.WA_WEB_INGEST_TOKEN || '').trim();
const DATA_PATH = (process.env.WA_WEB_DATA_PATH || './.wwebjs_auth').trim();

let latestQR = '';
let ready = false;
let authenticated = false;
let lastError = '';

function auth(req, res, next) {
  if (API_KEY && req.header('X-WA-WEB-KEY') !== API_KEY) {
    return res.status(403).json({ ok: false, error: 'forbidden' });
  }
  return next();
}

async function ingestMessage(payload) {
  if (!INGEST_URL) return;
  try {
    await fetch(INGEST_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-WA-WEB-KEY': INGEST_TOKEN || ''
      },
      body: JSON.stringify(payload)
    });
  } catch (err) {
    // ignore
  }
}

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: DATA_PATH }),
  puppeteer: {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  }
});

client.on('qr', (qr) => {
  latestQR = qr;
  ready = false;
  authenticated = false;
});

client.on('authenticated', () => {
  authenticated = true;
});

client.on('ready', () => {
  ready = true;
  lastError = '';
});

client.on('auth_failure', (msg) => {
  lastError = msg || 'auth failure';
  ready = false;
  authenticated = false;
});

client.on('disconnected', (reason) => {
  lastError = reason || 'disconnected';
  ready = false;
  authenticated = false;
});

client.on('message', async (msg) => {
  try {
    const from = msg.from || '';
    if (from.endsWith('@g.us')) {
      return;
    }
    const phone = from.replace('@c.us', '');
    const metaId = (msg.id && (msg.id.id || msg.id._serialized)) || '';
    const notifyName = msg._data && msg._data.notifyName ? msg._data.notifyName : '';
    const body = msg.body || '';

    await ingestMessage({
      from: phone,
      name: notifyName,
      body: body,
      direction: 'in',
      meta_id: metaId
    });
  } catch (err) {
    // ignore
  }
});

app.get('/health', (req, res) => {
  res.json({ ok: true });
});

app.get('/status', auth, (req, res) => {
  res.json({
    ok: true,
    ready: ready,
    authenticated: authenticated,
    qr: !!latestQR,
    error: lastError || ''
  });
});

app.get('/qr', auth, async (req, res) => {
  if (!latestQR) {
    return res.json({ ok: false, error: 'qr_not_ready' });
  }
  try {
    const dataUrl = await qrcode.toDataURL(latestQR);
    return res.json({ ok: true, qr: dataUrl });
  } catch (err) {
    return res.json({ ok: false, error: 'qr_failed' });
  }
});

app.post('/send', auth, async (req, res) => {
  const to = (req.body.to || '').trim();
  const text = (req.body.text || '').trim();
  if (!to || !text) {
    return res.status(400).json({ ok: false, error: 'to and text required' });
  }
  if (!ready) {
    return res.status(400).json({ ok: false, error: 'client not ready' });
  }
  const chatId = to.includes('@') ? to : `${to}@c.us`;
  try {
    await client.sendMessage(chatId, text);
    return res.json({ ok: true });
  } catch (err) {
    return res.status(500).json({ ok: false, error: 'send failed' });
  }
});

app.post('/logout', auth, async (req, res) => {
  try {
    await client.logout();
    latestQR = '';
    ready = false;
    authenticated = false;
    return res.json({ ok: true });
  } catch (err) {
    return res.status(500).json({ ok: false, error: 'logout failed' });
  }
});

client.initialize();

app.listen(PORT, () => {
  console.log(`WhatsApp Web gateway listening on ${PORT}`);
});
