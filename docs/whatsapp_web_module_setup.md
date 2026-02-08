# WhatsApp Web Module Setup (QR login)

This module uses a local WhatsApp Web session with QR login. It runs a small Node service on the server and connects to Django via a local webhook.

## 1) Django env vars (.env)

Add to `/Users/hossain/iconic_portal/.env` (or server `.env`):

```
WHATSAPP_ENABLED=1
WHATSAPP_AUTOMATION_ENABLED=1
WHATSAPP_OUTBOUND_ENABLED=1
WHATSAPP_PHONE_NUMBER=6045006009

WHATSAPP_SERVICE_URL=http://127.0.0.1:3127
WHATSAPP_SERVICE_SECRET=change_this_secret
WHATSAPP_WEBHOOK_SECRET=change_this_webhook_secret
WHATSAPP_SESSION_PATH=/var/lib/iconic_whatsapp

WHATSAPP_DAILY_LIMIT=120
WHATSAPP_HOURLY_LIMIT=20
WHATSAPP_CONTACT_DAILY_LIMIT=3
WHATSAPP_BUSINESS_HOURS_JSON={"start":"09:00","end":"17:00"}
```

Restart gunicorn after changes.

## 2) Node service install

On the AWS server:

```
cd /home/ec2-user/iconic_portal/whatsapp/node_service
npm install
```

Make sure Chromium and headless dependencies are installed (example for Amazon Linux 2023):

```
sudo dnf install -y chromium
```

## 3) Systemd service

Create `/etc/systemd/system/iconic-whatsapp.service`:

```
[Unit]
Description=Iconic WhatsApp Web Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ec2-user/iconic_portal/whatsapp/node_service
Environment=WHATSAPP_SERVICE_HOST=127.0.0.1
Environment=WHATSAPP_SERVICE_PORT=3127
Environment=WHATSAPP_SERVICE_SECRET=change_this_secret
Environment=WHATSAPP_WEBHOOK_URL=https://femline.ca/whatsapp/webhook/
Environment=WHATSAPP_WEBHOOK_SECRET=change_this_webhook_secret
Environment=WHATSAPP_SESSION_PATH=/var/lib/iconic_whatsapp
ExecStart=/usr/bin/node /home/ec2-user/iconic_portal/whatsapp/node_service/index.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now iconic-whatsapp
sudo systemctl status iconic-whatsapp
```

## 4) Run Django migrations

```
cd /home/ec2-user/iconic_portal
source venv/bin/activate
python manage.py migrate
```

## 5) QR login

Open the CRM page:

- `https://femline.ca/whatsapp/settings/`

Scan the QR code from WhatsApp on the phone (Linked Devices). Once connected, the status will show `connected`.

## 6) Background jobs

Run these from cron or a supervisor:

```
# every 5 minutes
python manage.py whatsapp_health_check

# every minute
python manage.py whatsapp_send_queue_worker

# daily (no-reply followups)
python manage.py whatsapp_followup_scheduler
```

## 7) Troubleshooting

- If QR does not appear: restart the Node service and refresh the Settings page.
- If sends fail: check `journalctl -u iconic-whatsapp` and Django logs for errors.
- If messages are not appearing: verify `WHATSAPP_WEBHOOK_URL` and `WHATSAPP_WEBHOOK_SECRET` match Django settings.

## 8) Security notes

- The Node service is bound to `127.0.0.1` and uses `WHATSAPP_SERVICE_SECRET` for local calls.
- Never expose the service port publicly.
