from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import SocialAccount, SocialContent
from marketing.services.linkedin import (
    fetch_linkedin_content,
    fetch_linkedin_metrics,
    fetch_linkedin_post_metrics,
    fetch_linkedin_account_metrics,
    fetch_linkedin_audience,
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
    help = "Sync LinkedIn daily data."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", default="")

    def _token_for_account(self, account):
        return token_for_social_account(account)

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SOCIAL_ENABLED", False):
            self.stdout.write("MARKETING_SOCIAL_ENABLED is off. Skipping.")
            return

        accounts = SocialAccount.objects.filter(is_active=True, platform="linkedin")
        account_id = (options.get("account_id") or "").strip()
        if account_id:
            accounts = accounts.filter(external_account_id=account_id)
        if not accounts.exists():
            self.stdout.write("No matching LinkedIn accounts found.")
            return

        synced_count = 0
        error_count = 0
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

                content_rows = fetch_linkedin_content(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for row in content_rows:
                    if not row.get("external_content_id"):
                        continue
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
                    if row.get("metric_payload"):
                        upsert_social_metric_daily(content_obj=content, payload=row["metric_payload"])

                    metric_rows = fetch_linkedin_post_metrics(
                        access_token=token,
                        account_id=acct.external_account_id,
                        content_id=row.get("external_content_id"),
                        start_date=start,
                        end_date=end,
                    )
                    if not metric_rows:
                        metric_rows = fetch_linkedin_metrics(
                            access_token=token,
                            content_id=row.get("external_content_id"),
                            start_date=start,
                            end_date=end,
                        )
                    for metric in metric_rows:
                        if metric:
                            upsert_social_metric_daily(content_obj=content, payload=metric)

                account_metric_rows = fetch_linkedin_account_metrics(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                )
                for metric in account_metric_rows:
                    upsert_account_metric_daily(account_obj=acct, payload=metric)

                audience_rows = fetch_linkedin_audience(
                    access_token=token,
                    account_id=acct.external_account_id,
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
                synced_count += 1
            except MarketingServiceError as exc:
                acct.last_sync_status = "error"
                acct.last_sync_message = str(exc)
                acct.save(update_fields=["last_sync_status", "last_sync_message"])
                update_connection_sync_state(acct, status="error", error=str(exc))
                error_count += 1
                self.stdout.write(self.style.ERROR(f"{acct.display_name or acct.external_account_id} LinkedIn sync failed: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"LinkedIn sync complete. synced={synced_count} errors={error_count}"))
