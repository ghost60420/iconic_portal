from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import SeoProperty
from marketing.services.gsc import fetch_gsc_query_daily, fetch_gsc_page_daily
from marketing.services.upsert import upsert_seo_query_daily, upsert_seo_page_daily
from marketing.services.errors import MarketingServiceError
from marketing.services.google_oauth import get_google_credential, get_valid_access_token
from marketing.utils.activity import log_marketing_sync_failure


class Command(BaseCommand):
    help = "Sync Google Search Console daily data."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_ENABLED", False):
            self.stdout.write("MARKETING_ENABLED is off. Skipping.")
            return

        creds = get_google_credential(fallback_platform="gsc")
        if not creds:
            self.stdout.write("No Google Search Console credential found. Skipping.")
            return
        try:
            token = get_valid_access_token(creds)
        except MarketingServiceError as exc:
            creds.last_sync_status = "error"
            creds.last_error = str(exc)
            creds.save(update_fields=["last_sync_status", "last_error", "updated_at"])
            log_marketing_sync_failure(
                platform="gsc",
                message=str(exc),
                model_label="marketing.OAuthCredential",
                object_id=creds.pk,
            )
            self.stdout.write(self.style.ERROR(str(exc)))
            return

        for prop in SeoProperty.objects.filter(is_active=True):
            if not prop.gsc_site_url:
                continue
            try:
                if prop.last_sync_at and prop.last_sync_status == "ok":
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

                synced_at = timezone.now()
                prop.last_sync_at = synced_at
                prop.last_sync_status = "ok"
                prop.last_sync_message = ""
                prop.save(update_fields=["last_sync_at", "last_sync_status", "last_sync_message"])
            except MarketingServiceError as exc:
                prop.last_sync_status = "error"
                prop.last_sync_message = str(exc)
                prop.save(update_fields=["last_sync_status", "last_sync_message"])
                log_marketing_sync_failure(
                    platform="gsc",
                    message=str(exc),
                    model_label="marketing.SeoProperty",
                    object_id=prop.pk,
                    meta={"site_url": prop.gsc_site_url},
                )
                self.stdout.write(self.style.ERROR(f"{prop.name} GSC sync failed: {exc}"))

        creds.last_synced_at = timezone.now()
        creds.last_sync_status = "ok"
        creds.last_error = ""
        creds.save(update_fields=["last_synced_at", "last_sync_status", "last_error", "updated_at"])

        self.stdout.write(self.style.SUCCESS("GSC sync complete."))
