from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from marketing.models import OAuthCredential
from marketing.services.google_oauth import get_google_credential, sync_google_properties
from marketing.services.errors import MarketingServiceError
from marketing.utils.activity import log_marketing_sync_failure


class Command(BaseCommand):
    help = "Sync Google Marketing data: discover accounts, then pull GA4, Search Console, YouTube, and Business Profile."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_ENABLED", False):
            self.stdout.write("MARKETING_ENABLED is off. Skipping.")
            return

        credential = get_google_credential()
        if not credential:
            self.stdout.write("No connected Google account found. Skipping.")
            return

        try:
            discovery = sync_google_properties(credential=credential)
            self.stdout.write(
                "Google discovery complete. "
                f"GA4: {discovery['ga4_count']} | "
                f"GSC: {discovery['gsc_count']} | "
                f"YouTube: {discovery.get('youtube_count', 0)} | "
                f"Business Profile: {discovery.get('google_business_count', 0)}"
            )
        except MarketingServiceError as exc:
            credential.last_sync_status = "error"
            credential.last_error = str(exc)
            credential.save(update_fields=["last_sync_status", "last_error", "updated_at"])
            log_marketing_sync_failure(
                platform="google",
                message=str(exc),
                model_label="marketing.OAuthCredential",
                object_id=credential.pk,
            )
            self.stdout.write(self.style.ERROR(f"Google discovery failed: {exc}"))
            return

        call_command("marketing_sync_ga4_daily", stdout=self.stdout, stderr=self.stderr)
        call_command("marketing_sync_gsc_daily", stdout=self.stdout, stderr=self.stderr)
        call_command("marketing_sync_youtube_daily", stdout=self.stdout, stderr=self.stderr)
        call_command("marketing_sync_google_business_daily", stdout=self.stdout, stderr=self.stderr)

        OAuthCredential.objects.filter(pk=credential.pk).update(last_sync_status="ok", last_error="")
        self.stdout.write(self.style.SUCCESS("Google marketing sync complete."))
