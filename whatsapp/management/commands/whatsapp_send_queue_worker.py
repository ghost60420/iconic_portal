import random
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from whatsapp.models import WhatsAppSendQueue, WhatsAppMessage, WhatsAppEventLog
from whatsapp.services import client as wa_client
from whatsapp.utils.limits import (
    is_dnc,
    contact_daily_count,
    account_daily_count,
    account_hourly_count,
)


class Command(BaseCommand):
    help = "Process WhatsApp outbound send queue."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Run continuously")
        parser.add_argument("--limit", type=int, default=50, help="Max items per run")

    def handle(self, *args, **options):
        if not getattr(settings, "WHATSAPP_OUTBOUND_ENABLED", False):
            self.stdout.write("WHATSAPP_OUTBOUND_ENABLED is off")
            return

        loop = options["loop"]
        limit = options["limit"]

        while True:
            processed = self._process_once(limit)
            if not loop:
                break
            time.sleep(10 if processed else 20)

    def _process_once(self, limit: int) -> int:
        now = timezone.now()
        daily_limit = getattr(settings, "WHATSAPP_DAILY_LIMIT", 120)
        hourly_limit = getattr(settings, "WHATSAPP_HOURLY_LIMIT", 20)
        contact_limit = getattr(settings, "WHATSAPP_CONTACT_DAILY_LIMIT", 3)

        items = (
            WhatsAppSendQueue.objects.select_related("thread", "account")
            .filter(status="queued", scheduled_at__lte=now)
            .order_by("scheduled_at", "id")[:limit]
        )

        count = 0
        for item in items:
            thread = item.thread
            account = item.account

            if is_dnc(thread.contact_phone):
                item.status = "canceled"
                item.last_error = "Do Not Contact"
                item.save(update_fields=["status", "last_error", "updated_at"])
                continue

            if contact_daily_count(thread) >= contact_limit:
                item.status = "canceled"
                item.last_error = "Contact daily limit reached"
                item.save(update_fields=["status", "last_error", "updated_at"])
                continue

            if account_daily_count(account) >= daily_limit:
                break

            if account_hourly_count(account) >= hourly_limit:
                break

            if item.attempts >= 3:
                item.status = "failed"
                item.last_error = "Max attempts"
                item.save(update_fields=["status", "last_error", "updated_at"])
                continue

            item.status = "processing"
            item.attempts += 1
            item.save(update_fields=["status", "attempts", "updated_at"])

            payload = {
                "chat_id": thread.wa_chat_id,
                "phone": thread.contact_phone,
                "message": item.message_body,
            }
            resp = wa_client.send_message(payload)
            if resp.get("ok"):
                wa_id = resp.get("message_id") or f"out-{item.id}"
                WhatsAppMessage.objects.get_or_create(
                    thread=thread,
                    wa_message_id=wa_id,
                    defaults={
                        "direction": "outbound",
                        "body": item.message_body,
                        "status": "sent",
                        "sent_at": timezone.now(),
                    },
                )
                thread.last_message_at = timezone.now()
                thread.save(update_fields=["last_message_at", "updated_at"])

                item.status = "sent"
                item.save(update_fields=["status", "updated_at"])
                WhatsAppEventLog.objects.create(
                    account=account,
                    thread=thread,
                    event="send",
                    level="info",
                    payload_json=resp,
                )
            else:
                item.status = "failed"
                item.last_error = resp.get("error", "Send failed")
                item.save(update_fields=["status", "last_error", "updated_at"])
                WhatsAppEventLog.objects.create(
                    account=account,
                    thread=thread,
                    event="send_failed",
                    level="error",
                    payload_json={"error": item.last_error},
                )

            count += 1
            time.sleep(random.uniform(0.4, 1.4))

        return count
