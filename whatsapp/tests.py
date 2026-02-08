import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from whatsapp.models import WhatsAppThread, WhatsAppMessage


class WhatsAppWebhookTests(TestCase):
    @override_settings(WHATSAPP_ENABLED=True, WHATSAPP_WEBHOOK_SECRET="secret")
    def test_webhook_rejects_bad_secret(self):
        client = Client()
        resp = client.post(
            reverse("wa_webhook"),
            data=json.dumps({"event": "message"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    @override_settings(WHATSAPP_ENABLED=True, WHATSAPP_WEBHOOK_SECRET="secret")
    def test_webhook_creates_thread_and_message(self):
        payload = {
            "event": "message",
            "chat_id": "16045551234@c.us",
            "from": "16045551234",
            "body": "Hello",
            "message_id": "m1",
            "contact_name": "Test User",
        }
        client = Client()
        resp = client.post(
            reverse("wa_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_WHATSAPP_SECRET="secret",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WhatsAppThread.objects.count(), 1)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)
        thread = WhatsAppThread.objects.first()
        self.assertEqual(thread.contact_phone, "16045551234")
        self.assertIsNotNone(thread.linked_lead)
