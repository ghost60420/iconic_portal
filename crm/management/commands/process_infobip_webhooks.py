from django.conf import settings
from django.core.management.base import BaseCommand

from crm.models_whatsapp import WhatsAppWebhookEvent
from crm.views_whatsapp import _process_infobip_event


class Command(BaseCommand):
    help = "Process pending Infobip WhatsApp webhook events."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Max events to process.")
        parser.add_argument(
            "--status",
            type=str,
            default="new,failed",
            help="Comma separated statuses to process.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "WHATSAPP_ENABLED", False):
            self.stdout.write("WHATSAPP_ENABLED is off")
            return
        statuses = [s.strip() for s in (options.get("status") or "new,failed").split(",") if s.strip()]
        qs = WhatsAppWebhookEvent.objects.filter(provider="infobip", status__in=statuses).order_by("received_at")
        if options.get("limit"):
            qs = qs[: options["limit"]]
        count = 0
        for event in qs:
            _process_infobip_event(event.pk)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {count} Infobip webhook events."))
