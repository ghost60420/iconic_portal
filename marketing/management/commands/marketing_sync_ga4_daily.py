from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import OAuthCredential, SeoProperty
from marketing.services.ga4 import fetch_ga4_daily
from marketing.services.errors import MarketingServiceError


class Command(BaseCommand):
    help = "Sync GA4 daily data."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SEO_ENABLED", False):
            self.stdout.write("MARKETING_SEO_ENABLED is off. Skipping.")
            return

        creds = OAuthCredential.objects.filter(platform="ga4").first()
        token = creds.get_access_token() if creds else ""

        for prop in SeoProperty.objects.filter(is_active=True):
            if not prop.ga4_property_id:
                continue
            try:
                if prop.last_sync_at:
                    start = prop.last_sync_at.date() + timedelta(days=1)
                else:
                    start = timezone.localdate() - timedelta(days=30)
                end = timezone.localdate() - timedelta(days=1)
                if start > end:
                    start = end

                fetch_ga4_daily(
                    access_token=token,
                    property_id=prop.ga4_property_id,
                    start_date=start,
                    end_date=end,
                )

                prop.last_sync_at = timezone.now()
                prop.last_sync_status = "ok"
                prop.last_sync_message = ""
                prop.save(update_fields=["last_sync_at", "last_sync_status", "last_sync_message"])
            except MarketingServiceError as exc:
                prop.last_sync_status = "error"
                prop.last_sync_message = str(exc)
                prop.save(update_fields=["last_sync_status", "last_sync_message"])

        self.stdout.write(self.style.SUCCESS("GA4 sync complete."))
