from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import SocialAccount, SocialContent
from marketing.services.youtube import (
    fetch_youtube_content,
    fetch_youtube_metrics,
    fetch_youtube_account_metrics,
    fetch_youtube_audience,
)
from marketing.services.upsert import (
    upsert_social_metric_daily,
    upsert_account_metric_daily,
    upsert_social_audience_daily,
)
from marketing.services.errors import MarketingServiceError
from marketing.services.oauth_connections import token_for_social_account
from marketing.services.social_connections import update_connection_sync_state


class Command(BaseCommand):
    help = "Sync YouTube daily data."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", default="")

    def _token_for_account(self, account):
        return token_for_social_account(account)

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SOCIAL_ENABLED", False):
            self.stdout.write("MARKETING_SOCIAL_ENABLED is off. Skipping.")
            return

        accounts = SocialAccount.objects.filter(is_active=True, platform="youtube")
        account_id = (options.get("account_id") or "").strip()
        if account_id:
            accounts = accounts.filter(external_account_id=account_id)
        if not accounts.exists():
            self.stdout.write("No matching YouTube accounts found.")
            return

        for acct in accounts:
            try:
                token = self._token_for_account(acct)
                today = timezone.localdate()
                if acct.last_successful_sync:
                    start = today - timedelta(days=1)
                    end = today - timedelta(days=1)
                else:
                    start = today - timedelta(days=30)
                    end = today - timedelta(days=1)

                content_rows = fetch_youtube_content(
                    access_token=token,
                    channel_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for row in content_rows:
                    content, _ = SocialContent.objects.update_or_create(
                        platform=acct.platform,
                        external_content_id=row.get("external_content_id"),
                        defaults={
                            "account": acct,
                            "content_type": row.get("content_type") or "video",
                            "title": row.get("title") or "",
                            "message_text": row.get("message_text") or "",
                            "permalink": row.get("permalink") or "",
                            "published_at": row.get("published_at"),
                        },
                    )

                    metric_rows = fetch_youtube_metrics(
                        access_token=token,
                        content_id=row.get("external_content_id"),
                        start_date=start,
                        end_date=end,
                    )
                    for metric in metric_rows:
                        upsert_social_metric_daily(content_obj=content, payload=metric)

                account_metric_rows = fetch_youtube_account_metrics(
                    access_token=token,
                    channel_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for metric in account_metric_rows:
                    upsert_account_metric_daily(account_obj=acct, payload=metric)

                audience_rows = fetch_youtube_audience(
                    access_token=token,
                    channel_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for row in audience_rows:
                    upsert_social_audience_daily(account_obj=acct, payload=row)

                synced_at = timezone.now()
                acct.last_sync_at = synced_at
                acct.last_successful_sync = synced_at
                acct.last_sync_status = "ok"
                acct.last_sync_message = ""
                acct.save(update_fields=["last_sync_at", "last_successful_sync", "last_sync_status", "last_sync_message"])
                update_connection_sync_state(acct, status="ok", synced_at=synced_at)
            except MarketingServiceError as exc:
                acct.last_sync_status = "error"
                acct.last_sync_message = str(exc)
                acct.save(update_fields=["last_sync_status", "last_sync_message"])
                update_connection_sync_state(acct, status="error", error=str(exc))

        self.stdout.write(self.style.SUCCESS("YouTube sync complete."))
