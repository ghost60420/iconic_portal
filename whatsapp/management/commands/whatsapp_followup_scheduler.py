from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from whatsapp.models import WhatsAppAutomationRule, WhatsAppThread, WhatsAppSendQueue
from whatsapp.utils.automation import enqueue_rule
from whatsapp.utils.limits import is_dnc


class Command(BaseCommand):
    help = "Queue no-reply followups for WhatsApp threads."

    def handle(self, *args, **options):
        if not getattr(settings, "WHATSAPP_AUTOMATION_ENABLED", False):
            self.stdout.write("WHATSAPP_AUTOMATION_ENABLED is off")
            return

        rule = WhatsAppAutomationRule.objects.filter(is_active=True, trigger="no_reply_followup").first()
        if not rule:
            self.stdout.write("No no_reply_followup rule found")
            return

        delay_seconds = rule.send_delay_seconds or 86400
        cutoff = timezone.now() - timedelta(seconds=delay_seconds)

        threads = WhatsAppThread.objects.filter(automation_enabled=True, last_message_at__lte=cutoff)
        queued = 0
        for thread in threads:
            if is_dnc(thread.contact_phone):
                continue

            last_out = thread.messages.filter(direction="outbound", sent_at__isnull=False).order_by("-sent_at").first()
            if not last_out or last_out.sent_at > cutoff:
                continue

            last_in = thread.messages.filter(direction="inbound", received_at__isnull=False).order_by("-received_at").first()
            if last_in and last_in.received_at > last_out.sent_at:
                continue

            recent_queue = WhatsAppSendQueue.objects.filter(
                thread=thread,
                created_at__gte=timezone.now() - timedelta(days=1),
            ).exists()
            if recent_queue:
                continue

            enqueue_rule(thread, rule, lead=thread.linked_lead)
            queued += 1

        self.stdout.write(f"Queued followups: {queued}")
