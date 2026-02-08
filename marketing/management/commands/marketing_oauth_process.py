from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import OAuthConnectionRequest, OAuthCredential, SocialAccount
from marketing.services.errors import MarketingServiceError
from marketing.services.oauth_meta import (
    exchange_code_for_token,
    exchange_long_lived_token,
    fetch_meta_pages,
)


class Command(BaseCommand):
    help = "Process pending OAuth connection requests (Meta)."

    def add_arguments(self, parser):
        parser.add_argument("--platform", default="meta")

    def handle(self, *args, **options):
        platform = options.get("platform") or "meta"
        if platform != "meta":
            self.stdout.write("Only meta platform is supported right now.")
            return

        app_id = getattr(settings, "MARKETING_META_APP_ID", "")
        app_secret = getattr(settings, "MARKETING_META_APP_SECRET", "")
        redirect_uri = getattr(settings, "MARKETING_META_REDIRECT_URI", "")
        scopes = getattr(settings, "MARKETING_META_SCOPES", [])
        if not app_id or not app_secret or not redirect_uri:
            self.stdout.write("Meta app is not configured.")
            return

        pending = OAuthConnectionRequest.objects.filter(platform="meta", status="received").exclude(code="")
        if not pending.exists():
            self.stdout.write("No pending OAuth requests.")
            return

        for req in pending:
            try:
                token_payload = exchange_code_for_token(
                    app_id=app_id,
                    app_secret=app_secret,
                    redirect_uri=redirect_uri,
                    code=req.code,
                )
                short_token = token_payload.get("access_token")
                if not short_token:
                    raise MarketingServiceError("Meta token exchange failed.")

                long_payload = exchange_long_lived_token(
                    app_id=app_id,
                    app_secret=app_secret,
                    access_token=short_token,
                )
                access_token = long_payload.get("access_token") or short_token
                expires_in = long_payload.get("expires_in") or token_payload.get("expires_in")
                expires_at = None
                if expires_in:
                    try:
                        expires_at = timezone.now() + timedelta(seconds=int(expires_in))
                    except Exception:
                        expires_at = None

                platform_cred, _ = OAuthCredential.objects.get_or_create(platform="meta", platform_account=None)
                platform_cred.set_tokens(access_token=access_token, refresh_token="", expires_at=expires_at)
                platform_cred.scopes = ",".join(scopes)
                platform_cred.save()

                pages = fetch_meta_pages(access_token=access_token)
                for page in pages:
                    page_id = page.get("id")
                    if not page_id:
                        continue
                    name = page.get("name") or "Facebook Page"
                    timezone_name = page.get("timezone") or ""

                    fb_account, _ = SocialAccount.objects.update_or_create(
                        platform="facebook",
                        external_account_id=page_id,
                        defaults={
                            "display_name": name,
                            "timezone": timezone_name,
                            "is_active": True,
                        },
                    )
                    fb_cred, _ = OAuthCredential.objects.get_or_create(platform="facebook", platform_account=fb_account)
                    fb_cred.set_tokens(access_token=access_token, refresh_token="", expires_at=expires_at)
                    fb_cred.scopes = ",".join(scopes)
                    fb_cred.save()

                    ig_info = page.get("instagram_business_account") or {}
                    ig_id = ig_info.get("id")
                    if ig_id:
                        ig_account, _ = SocialAccount.objects.update_or_create(
                            platform="instagram",
                            external_account_id=ig_id,
                            defaults={
                                "display_name": f"{name} (Instagram)",
                                "timezone": timezone_name,
                                "is_active": True,
                            },
                        )
                        ig_cred, _ = OAuthCredential.objects.get_or_create(
                            platform="instagram",
                            platform_account=ig_account,
                        )
                        ig_cred.set_tokens(access_token=access_token, refresh_token="", expires_at=expires_at)
                        ig_cred.scopes = ",".join(scopes)
                        ig_cred.save()

                req.status = "completed"
                req.error_message = ""
                req.save(update_fields=["status", "error_message", "updated_at"])
            except MarketingServiceError as exc:
                req.status = "error"
                req.error_message = str(exc)
                req.save(update_fields=["status", "error_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS("OAuth processing complete."))
