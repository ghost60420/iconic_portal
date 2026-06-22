from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import SeoProperty
from marketing.services.ga4 import fetch_ga4_daily
from marketing.services.upsert import upsert_website_traffic_daily, upsert_website_page_daily
from marketing.services.errors import MarketingServiceError
from marketing.services.google_oauth import get_google_credential, get_valid_access_token
from marketing.utils.activity import log_marketing_sync_failure


class Command(BaseCommand):
    help = "Sync GA4 daily data."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_ENABLED", False):
            self.stdout.write("MARKETING_ENABLED is off. Skipping.")
            return

        creds = get_google_credential(fallback_platform="ga4")
        if not creds:
            self.stdout.write("No Google Analytics credential found. Skipping.")
            return
        try:
            token = get_valid_access_token(creds)
        except MarketingServiceError as exc:
            creds.last_sync_status = "error"
            creds.last_error = str(exc)
            creds.save(update_fields=["last_sync_status", "last_error", "updated_at"])
            log_marketing_sync_failure(
                platform="ga4",
                message=str(exc),
                model_label="marketing.OAuthCredential",
                object_id=creds.pk,
            )
            self.stdout.write(self.style.ERROR(str(exc)))
            return

        for prop in SeoProperty.objects.filter(is_active=True):
            if not prop.ga4_property_id:
                continue
            try:
                if prop.last_sync_at and prop.last_sync_status == "ok":
                    start = prop.last_sync_at.date() + timedelta(days=1)
                else:
                    start = timezone.localdate() - timedelta(days=30)
                end = timezone.localdate() - timedelta(days=1)
                if start > end:
                    start = end

                sync_payload = fetch_ga4_daily(
                    access_token=token,
                    property_id=prop.ga4_property_id,
                    start_date=start,
                    end_date=end,
                )
                traffic_rows = sync_payload.get("traffic_rows", []) if isinstance(sync_payload, dict) else sync_payload
                page_rows = sync_payload.get("page_rows", []) if isinstance(sync_payload, dict) else []
                for row in traffic_rows or []:
                    upsert_website_traffic_daily(property_obj=prop, payload=row)
                for row in page_rows or []:
                    upsert_website_page_daily(property_obj=prop, payload=row)

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
                    platform="ga4",
                    message=str(exc),
                    model_label="marketing.SeoProperty",
                    object_id=prop.pk,
                    meta={"property_id": prop.ga4_property_id},
                )
                self.stdout.write(self.style.ERROR(f"{prop.name} GA4 sync failed: {exc}"))

        creds.last_synced_at = timezone.now()
        creds.last_sync_status = "ok"
        creds.last_error = ""
        creds.save(update_fields=["last_synced_at", "last_sync_status", "last_error", "updated_at"])

        self.stdout.write(self.style.SUCCESS("GA4 sync complete."))
