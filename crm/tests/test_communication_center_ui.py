from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models_access import UserAccess
from crm.models_email import EmailMessage, EmailThread
from crm.models_whatsapp import WhatsAppMessage, WhatsAppThread, WhatsAppWebhookEvent


@override_settings(OPENAI_API_KEY="", WHATSAPP_ENABLED=True, WA_PROVIDER="meta")
class CommunicationCenterUITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="communication-ui-admin",
            email="communication-ui-admin@example.com",
            password="pass",
        )
        UserAccess.objects.update_or_create(
            user=self.user,
            defaults={
                "can_ai": True,
                "can_whatsapp": True,
                "can_view_ceo_tools": True,
            },
        )
        self.client.force_login(self.user)

        now = timezone.now()
        self.email_thread = EmailThread.objects.create(
            label="lead",
            mailbox="lead@example.com",
            subject="Phase D inquiry",
            from_email="client@example.com",
            from_name="Client",
            last_message_at=now,
        )
        EmailMessage.objects.create(
            thread=self.email_thread,
            imap_uid="phase-d-email-1",
            subject="Phase D inquiry",
            from_email="client@example.com",
            from_name="Client",
            to_email="lead@example.com",
            body_text="Need quote and follow up",
            is_form_entry=True,
            is_lead_candidate=True,
        )

        self.wa_thread = WhatsAppThread.objects.create(
            wa_phone="+16045550123",
            wa_name="Client WA",
            last_message_at=now,
            last_inbound_at=now,
            needs_human=True,
            ai_enabled=True,
        )
        WhatsAppMessage.objects.create(
            thread=self.wa_thread,
            direction="in",
            body="Hello from client",
            status="received",
            meta_id="phase-d-wa-in",
        )
        WhatsAppMessage.objects.create(
            thread=self.wa_thread,
            direction="out",
            body="Reply from team",
            status="sent",
            meta_id="phase-d-wa-out",
        )
        WhatsAppWebhookEvent.objects.create(
            provider="infobip",
            raw_payload={"messages": [{"id": "phase-d"}]},
            status="processed",
            processed_at=now,
        )

    def test_email_center_preserves_sync_and_filters(self):
        response = self.client.get(reverse("email_sync_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "comm-modern")
        self.assertContains(response, "Communication Center")
        self.assertContains(response, reverse("email_sync_run"))
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'id="emailSearch"')
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'name="label"')
        self.assertContains(response, 'name="flag"')
        self.assertContains(response, "Phase D inquiry")

    def test_whatsapp_inbox_preserves_forms_attachments_and_actions(self):
        response = self.client.get(f"{reverse('wa_api_inbox')}?thread={self.wa_thread.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "comm-modern")
        self.assertContains(response, 'id="wa-csrf"')
        self.assertContains(response, 'id="sendForm"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'id="fileInput"')
        self.assertContains(response, 'name="file"')
        self.assertContains(response, 'id="msgBox"')
        self.assertContains(response, 'id="btnSend"')
        self.assertContains(response, 'id="btnToggle"')
        self.assertContains(response, 'id="waSearch"')
        self.assertContains(response, reverse("wa_infobip_events"))

    def test_message_logs_preserve_event_payload_history(self):
        response = self.client.get(reverse("wa_infobip_events"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "comm-modern")
        self.assertContains(response, "Message Logs")
        self.assertContains(response, "Webhook History")
        self.assertContains(response, "Processed")
        self.assertContains(response, "phase-d")

    def test_email_draft_page_preserves_copy_only_draft_controls(self):
        response = self.client.get(reverse("daily_ceo_briefing_email_draft"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "comm-modern")
        self.assertContains(response, "Email Draft Preview")
        self.assertContains(response, 'id="draft-subject"')
        self.assertContains(response, 'id="draft-body"')
        self.assertContains(response, 'data-copy-target="draft-body"')
        self.assertContains(response, reverse("daily_ceo_briefing"))
