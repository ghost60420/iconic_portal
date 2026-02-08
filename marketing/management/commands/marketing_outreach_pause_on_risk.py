from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import OutreachCampaign, OutreachSendLog


class Command(BaseCommand):
    help = "Pause outreach campaigns when bounce or failure rate is high."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_OUTREACH_ENABLED", False):
            self.stdout.write("MARKETING_OUTREACH_ENABLED is off. Skipping.")
            return

        since = timezone.now() - timedelta(days=7)
        paused = 0

        for campaign in OutreachCampaign.objects.filter(status="active", channel="email"):
            sent = OutreachSendLog.objects.filter(campaign=campaign, sent_at__gte=since, status="sent").count()
            failed = OutreachSendLog.objects.filter(campaign=campaign, sent_at__gte=since, status__in=["failed", "bounced"]).count()
            if sent >= 20:
                rate = failed / max(sent, 1)
                if rate >= 0.08:
                    campaign.status = "paused"
                    campaign.save(update_fields=["status"])
                    paused += 1

        self.stdout.write(self.style.SUCCESS(f"Paused {paused} campaigns."))
