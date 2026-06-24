from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import AdAccount, AdCampaign, OAuthCredential, SocialAccount, SocialContent
from marketing.services.meta import (
    fetch_meta_content,
    fetch_meta_metrics,
    fetch_meta_account_metrics,
    fetch_meta_audience,
    fetch_meta_ad_accounts,
    fetch_meta_ad_campaigns,
    fetch_meta_ad_insights,
)
from marketing.services.upsert import (
    upsert_social_metric_daily,
    upsert_account_metric_daily,
    upsert_social_audience_daily,
    upsert_ad_metric_daily,
)
from marketing.services.errors import MarketingServiceError
from marketing.services.oauth_connections import get_valid_oauth_access_token
from marketing.services.social_connections import update_connection_sync_state


class Command(BaseCommand):
    help = "Sync Meta (Facebook/Instagram) daily data."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", default="")
        parser.add_argument("--platform", default="")

    def _token_for_account(self, account):
        cred = OAuthCredential.objects.filter(platform_account=account).first()
        if cred and cred.get_access_token():
            return get_valid_oauth_access_token(cred)
        cred = OAuthCredential.objects.filter(platform=account.platform).first()
        if cred and cred.get_access_token():
            return get_valid_oauth_access_token(cred)
        cred = OAuthCredential.objects.filter(platform="meta").first()
        return get_valid_oauth_access_token(cred) if cred else ""

    def _sync_meta_ads(self, *, access_token: str, start, end):
        synced = 0
        ad_account_count = 0
        campaign_count = 0
        active_campaign_count = 0
        connected_account_names = []
        ad_accounts = fetch_meta_ad_accounts(access_token=access_token)
        for row in ad_accounts:
            ad_account_id = row.get("external_ad_account_id")
            if not ad_account_id:
                continue
            platform_account, _ = SocialAccount.objects.update_or_create(
                platform="meta_business",
                external_account_id=ad_account_id,
                defaults={"display_name": row.get("name") or "Meta Ad Account", "is_active": bool(row.get("is_active", True))},
            )
            ad_account_count += 1
            connected_account_names.append(platform_account.display_name or ad_account_id)
            ad_account, _ = AdAccount.objects.update_or_create(
                platform_account=platform_account,
                external_ad_account_id=ad_account_id,
                defaults={"currency": row.get("currency") or "", "is_active": bool(row.get("is_active", True))},
            )
            campaign_rows = fetch_meta_ad_campaigns(access_token=access_token, ad_account_id=ad_account_id)
            campaign_count += len(campaign_rows)
            for campaign_row in campaign_rows:
                campaign_id = campaign_row.get("external_campaign_id")
                if not campaign_id:
                    continue
                if (campaign_row.get("status") or "").upper() == "ACTIVE":
                    active_campaign_count += 1
                AdCampaign.objects.update_or_create(
                    ad_account=ad_account,
                    external_campaign_id=campaign_id,
                    defaults={
                        "name": campaign_row.get("name") or "",
                        "status": campaign_row.get("status") or "",
                        "objective": campaign_row.get("objective") or "",
                    },
                )
            for metric in fetch_meta_ad_insights(access_token=access_token, ad_account_id=ad_account_id, start_date=start, end_date=end):
                campaign, _ = AdCampaign.objects.update_or_create(
                    ad_account=ad_account,
                    external_campaign_id=metric.get("external_campaign_id") or "unknown",
                    defaults={"name": metric.get("campaign_name") or "", "status": "", "objective": ""},
                )
                upsert_ad_metric_daily(ad_campaign_obj=campaign, payload=metric)
                synced += 1

            synced_at = timezone.now()
            platform_account.last_sync_at = synced_at
            platform_account.last_successful_sync = synced_at
            platform_account.last_sync_status = "ok"
            platform_account.last_sync_message = ""
            platform_account.save(update_fields=["last_sync_at", "last_successful_sync", "last_sync_status", "last_sync_message"])
            update_connection_sync_state(platform_account, status="ok", synced_at=synced_at)
        message = (
            "No Meta ad activity detected"
            if synced == 0
            else f"Meta Ads rows synced: {synced}"
        )
        return {
            "synced_rows": synced,
            "ad_account_count": ad_account_count,
            "campaign_count": campaign_count,
            "active_campaign_count": active_campaign_count,
            "connected_account_names": connected_account_names,
            "message": message,
        }

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_SOCIAL_ENABLED", False):
            self.stdout.write("MARKETING_SOCIAL_ENABLED is off. Skipping.")
            return

        today = timezone.localdate()
        start = today - timedelta(days=30)
        end = today - timedelta(days=1)

        accounts = SocialAccount.objects.filter(is_active=True, platform__in=["facebook", "instagram"])
        account_id = (options.get("account_id") or "").strip()
        platform = (options.get("platform") or "").strip()
        if account_id:
            accounts = accounts.filter(external_account_id=account_id)
        if platform and platform != "meta_ads":
            accounts = accounts.filter(platform=platform)

        meta_token = ""
        meta_credential = OAuthCredential.objects.filter(platform="meta", is_active=True).order_by("-updated_at").first()
        if meta_credential:
            meta_token = get_valid_oauth_access_token(meta_credential)

        if not accounts.exists() and platform != "meta_ads":
            self.stdout.write("No matching Meta accounts found.")

        for acct in accounts:
            try:
                token = self._token_for_account(acct)
                if acct.last_successful_sync:
                    start = today - timedelta(days=1)
                    end = today - timedelta(days=1)
                else:
                    start = today - timedelta(days=30)
                    end = today - timedelta(days=1)

                content_rows = fetch_meta_content(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                    platform=acct.platform,
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

                    try:
                        metric_rows = fetch_meta_metrics(
                            access_token=token,
                            content_id=row.get("external_content_id"),
                            start_date=start,
                            end_date=end,
                            platform=acct.platform,
                        )
                    except MarketingServiceError:
                        metric_rows = []
                    for metric in metric_rows:
                        if metric:
                            upsert_social_metric_daily(content_obj=content, payload=metric)

                account_metric_rows = fetch_meta_account_metrics(
                    access_token=token,
                    account_id=acct.external_account_id,
                    start_date=start,
                    end_date=end,
                    platform=acct.platform,
                )
                for metric in account_metric_rows:
                    upsert_account_metric_daily(account_obj=acct, payload=metric)

                audience_rows = fetch_meta_audience(
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
            except MarketingServiceError as exc:
                acct.last_sync_status = "error"
                acct.last_sync_message = str(exc)
                acct.save(update_fields=["last_sync_status", "last_sync_message"])
                update_connection_sync_state(acct, status="error", error=str(exc))

        if platform in {"", "meta_ads"} and meta_token:
            try:
                meta_ads_result = self._sync_meta_ads(access_token=meta_token, start=start, end=end)
                if meta_credential:
                    meta_credential.last_sync_status = "ok"
                    meta_credential.last_error = ""
                    meta_credential.last_synced_at = timezone.now()
                    meta_credential.save(update_fields=["last_sync_status", "last_error", "last_synced_at", "updated_at"])
                self.stdout.write(meta_ads_result["message"])
                self.stdout.write(f"Connected ad accounts: {meta_ads_result['ad_account_count']}")
                self.stdout.write(f"Campaigns: {meta_ads_result['campaign_count']}")
                self.stdout.write(f"Active campaigns: {meta_ads_result['active_campaign_count']}")
                if meta_ads_result["connected_account_names"]:
                    self.stdout.write(f"Connected ad account names: {', '.join(meta_ads_result['connected_account_names'])}")
            except MarketingServiceError as exc:
                if meta_credential:
                    meta_credential.last_sync_status = "error"
                    meta_credential.last_error = str(exc)
                    meta_credential.save(update_fields=["last_sync_status", "last_error", "updated_at"])
                self.stderr.write(f"Meta Ads sync failed: {exc}")

        self.stdout.write(self.style.SUCCESS("Meta sync complete."))
