from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import OAuthCredential, SeoProperty
from marketing.services.gsc import fetch_gsc_query_daily, fetch_gsc_page_daily
from marketing.services.upsert import upsert_seo_query_daily, upsert_seo_page_daily
from marketing.services.errors import MarketingServiceError


class Command(BaseCommand):
    help = "Sync Google Search Console daily data."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SEO_ENABLED", False):
            self.stdout.write("MARKETING_SEO_ENABLED is off. Skipping.")
            return

        creds = OAuthCredential.objects.filter(platform="gsc").first()
        token = creds.get_access_token() if creds else ""

        for prop in SeoProperty.objects.filter(is_active=True):
            try:
                if prop.last_sync_at:
                    start = prop.last_sync_at.date() + timedelta(days=1)
                else:
                    start = timezone.localdate() - timedelta(days=30)
                end = timezone.localdate() - timedelta(days=1)
                if start > end:
                    start = end

                query_rows = fetch_gsc_query_daily(
                    access_token=token,
                    site_url=prop.gsc_site_url,
                    start_date=start,
                    end_date=end,
                )
                for row in query_rows:
                    upsert_seo_query_daily(property_obj=prop, payload=row)

                page_rows = fetch_gsc_page_daily(
                    access_token=token,
                    site_url=prop.gsc_site_url,
                    start_date=start,
                    end_date=end,
                )
                for row in page_rows:
                    upsert_seo_page_daily(property_obj=prop, payload=row)

                prop.last_sync_at = timezone.now()
                prop.last_sync_status = "ok"
                prop.last_sync_message = ""
                prop.save(update_fields=["last_sync_at", "last_sync_status", "last_sync_message"])
            except MarketingServiceError as exc:
                prop.last_sync_status = "error"
                prop.last_sync_message = str(exc)
                prop.save(update_fields=["last_sync_status", "last_sync_message"])

        self.stdout.write(self.style.SUCCESS("GSC sync complete."))
