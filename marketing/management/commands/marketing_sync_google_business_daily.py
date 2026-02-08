from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import OAuthCredential, SocialAccount, SocialContent
from marketing.services.google_business import (
    fetch_google_business_content,
    fetch_google_business_metrics,
    fetch_google_business_account_metrics,
    fetch_google_business_audience,
)
from marketing.services.upsert import (
    upsert_social_metric_daily,
    upsert_account_metric_daily,
    upsert_social_audience_daily,
)
from marketing.services.errors import MarketingServiceError


class Command(BaseCommand):
    help = "Sync Google Business Profile daily data."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SOCIAL_ENABLED", False):
            self.stdout.write("MARKETING_SOCIAL_ENABLED is off. Skipping.")
            return

        creds = OAuthCredential.objects.filter(platform="google_business").first()
        token = creds.get_access_token() if creds else ""

        for acct in SocialAccount.objects.filter(is_active=True, platform="google_business"):
            try:
                today = timezone.localdate()
                if acct.last_successful_sync:
                    start = today - timedelta(days=1)
                    end = today - timedelta(days=1)
                else:
                    start = today - timedelta(days=30)
                    end = today - timedelta(days=1)

                content_rows = fetch_google_business_content(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for row in content_rows:
                    content, _ = SocialContent.objects.update_or_create(
                        platform=acct.platform,
                        external_content_id=row.get("external_content_id"),
                        defaults={
                            "account": acct,
                            "content_type": row.get("content_type") or "post",
                            "title": row.get("title") or "",
                            "message_text": row.get("message_text") or "",
                            "permalink": row.get("permalink") or "",
                            "published_at": row.get("published_at"),
                        },
                    )

                    metric_rows = fetch_google_business_metrics(
                        access_token=token,
                        content_id=row.get("external_content_id"),
                        start_date=start,
                        end_date=end,
                    )
                    for metric in metric_rows:
                        upsert_social_metric_daily(content_obj=content, payload=metric)

                account_metric_rows = fetch_google_business_account_metrics(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for metric in account_metric_rows:
                    upsert_account_metric_daily(account_obj=acct, payload=metric)

                audience_rows = fetch_google_business_audience(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for row in audience_rows:
                    upsert_social_audience_daily(account_obj=acct, payload=row)

                acct.last_sync_at = timezone.now()
                acct.last_successful_sync = timezone.now()
                acct.last_sync_status = "ok"
                acct.last_sync_message = ""
                acct.save(update_fields=["last_sync_at", "last_successful_sync", "last_sync_status", "last_sync_message"])
            except MarketingServiceError as exc:
                acct.last_sync_status = "error"
                acct.last_sync_message = str(exc)
                acct.save(update_fields=["last_sync_status", "last_sync_message"])

        self.stdout.write(self.style.SUCCESS("Google Business sync complete."))
