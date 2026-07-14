from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import SocialAccount, SocialContent
from marketing.services.google_business import (
    fetch_google_business_content,
    fetch_google_business_metrics,
    fetch_google_business_account_metrics,
    fetch_google_business_audience,
)
from marketing.services.google_oauth import (
    discover_google_business_locations,
    get_google_credential,
    get_valid_access_token,
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
    help = "Sync Google Business Profile daily data."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", default="")

    def _token_for_account(self, account):
        return token_for_social_account(account)

    def _discover_google_business_accounts(self):
        credential = get_google_credential()
        if not credential:
            self.stdout.write(self.style.WARNING("No active Google credential found for Google Business discovery."))
            return {}

        try:
            token = get_valid_access_token(credential)
            discovery = discover_google_business_locations(token)
        except MarketingServiceError as exc:
            self.stdout.write(self.style.WARNING(f"Google Business discovery failed: {exc}"))
            return {}

        location_context = {}
        for item in discovery.get("locations", []):
            location_name = item.get("location_name") or ""
            if not location_name:
                continue
            account, _ = SocialAccount.objects.update_or_create(
                platform="google_business",
                external_account_id=location_name,
                defaults={
                    "display_name": item.get("title") or location_name,
                    "is_active": True,
                },
            )
            location_context[account.external_account_id] = item

        credential.last_sync_status = "connected"
        if "mybusiness" in (credential.last_error or "").lower() or "google business" in (credential.last_error or "").lower():
            credential.last_error = ""
        credential.last_synced_at = timezone.now()
        credential.save(update_fields=["last_sync_status", "last_error", "last_synced_at", "updated_at"])

        self.stdout.write(
            f"Google Business discovery complete: {len(discovery.get('accounts', []))} account(s), "
            f"{len(location_context)} location(s)."
        )
        return location_context

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SOCIAL_ENABLED", False):
            self.stdout.write("MARKETING_SOCIAL_ENABLED is off. Skipping.")
            return

        location_context = self._discover_google_business_accounts()
        accounts = SocialAccount.objects.filter(is_active=True, platform="google_business")
        account_id = (options.get("account_id") or "").strip()
        if account_id:
            accounts = accounts.filter(external_account_id=account_id)
        if not accounts.exists():
            self.stdout.write("No matching Google Business accounts found.")
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

                content_warning = ""
                try:
                    content_rows = fetch_google_business_content(
                        access_token=token,
                        account_id=acct.external_account_id,
                        start_date=start,
                        end_date=end,
                        business_account_name=(location_context.get(acct.external_account_id) or {}).get("account_name", ""),
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
                except MarketingServiceError as exc:
                    content_warning = f"Content sync warning: {exc}"
                    self.stdout.write(self.style.WARNING(f"{acct.display_name}: {content_warning}"))

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

                synced_at = timezone.now()
                acct.last_sync_at = synced_at
                acct.last_successful_sync = synced_at
                acct.last_sync_status = "ok"
                acct.last_sync_message = content_warning[:2000]
                acct.save(update_fields=["last_sync_at", "last_successful_sync", "last_sync_status", "last_sync_message"])
                update_connection_sync_state(acct, status="ok", synced_at=synced_at)
            except MarketingServiceError as exc:
                acct.last_sync_status = "error"
                acct.last_sync_message = str(exc)
                acct.save(update_fields=["last_sync_status", "last_sync_message"])
                update_connection_sync_state(acct, status="error", error=str(exc))

        self.stdout.write(self.style.SUCCESS("Google Business sync complete."))
