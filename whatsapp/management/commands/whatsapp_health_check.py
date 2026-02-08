from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from whatsapp.models import WhatsAppAccount, WhatsAppEventLog
from whatsapp.services import client as wa_client


class Command(BaseCommand):
    help = "Check WhatsApp web service health and update account status."

    def handle(self, *args, **options):
        if not getattr(settings, "WHATSAPP_ENABLED", False):
            self.stdout.write("WHATSAPP_ENABLED is off")
            return

        phone = getattr(settings, "WHATSAPP_PHONE_NUMBER", "6045006009")
        account, _ = WhatsAppAccount.objects.get_or_create(phone_number=phone)

        try:
            status = wa_client.get_status()
            account.status = status.get("status", "error")
            if account.status == "connected":
                account.last_seen_at = timezone.now()
            account.save(update_fields=["status", "last_seen_at", "updated_at"])
            WhatsAppEventLog.objects.create(account=account, event="health", payload_json=status)
        except Exception as exc:
            account.status = "error"
            account.save(update_fields=["status", "updated_at"])
            WhatsAppEventLog.objects.create(
                account=account,
                event="health_failed",
                level="error",
                payload_json={"error": str(exc)[:200]},
            )
            self.stderr.write(str(exc))
