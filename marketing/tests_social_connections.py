from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models_access import UserAccess
from marketing.models import OAuthCredential, SocialAccount
from marketing.services.social_connections import run_social_connection_sync, save_social_connection


@override_settings(MARKETING_ENABLED=True, MARKETING_SOCIAL_ENABLED=True)
class MarketingSocialConnectionsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="social-admin", password="pass1234")
        access, _ = UserAccess.objects.get_or_create(user=self.user)
        access.can_marketing = True
        access.save()
        self.client.login(username="social-admin", password="pass1234")

    def test_social_connections_page_renders(self):
        response = self.client.get(reverse("marketing_connect"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connection Settings")

    def test_save_connection_creates_encrypted_tokens_and_account(self):
        response = self.client.post(
            reverse("marketing_connect"),
            {
                "platform": "linkedin",
                "account_name": "Iconic LinkedIn",
                "account_id": "urn:li:organization:42",
                "access_token": "access-123",
                "refresh_token": "refresh-456",
                "token_expires_at": "",
                "scopes": "r_organization_social,r_organization_admin",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        credential = OAuthCredential.objects.get(platform="linkedin", account_id="urn:li:organization:42")
        self.assertEqual(credential.account_name, "Iconic LinkedIn")
        self.assertNotEqual(credential.encrypted_access_token, "access-123")
        self.assertEqual(credential.get_access_token(), "access-123")
        self.assertEqual(credential.get_refresh_token(), "refresh-456")
        self.assertTrue(
            SocialAccount.objects.filter(
                platform="linkedin",
                external_account_id="urn:li:organization:42",
                display_name="Iconic LinkedIn",
            ).exists()
        )

    def test_save_social_connection_preserves_existing_tokens_when_left_blank(self):
        credential = OAuthCredential.objects.create(
            platform="youtube",
            account_name="Iconic Channel",
            account_id="channel-1",
            is_active=True,
        )
        credential.set_tokens(access_token="existing-access", refresh_token="existing-refresh")
        credential.save()

        updated = save_social_connection(
            existing=credential,
            cleaned_data={
                "platform": "youtube",
                "account_name": "Iconic Channel",
                "account_id": "channel-1",
                "access_token": "",
                "refresh_token": "",
                "token_expires_at": None,
                "scopes": "youtube.readonly",
                "is_active": True,
            },
        )

        self.assertEqual(updated.get_access_token(), "existing-access")
        self.assertEqual(updated.get_refresh_token(), "existing-refresh")
        self.assertEqual(updated.scopes, "youtube.readonly")

    def test_run_social_connection_sync_uses_platform_command(self):
        account = SocialAccount.objects.create(
            platform="facebook",
            external_account_id="page-1",
            display_name="Iconic Page",
            is_active=True,
        )
        credential = OAuthCredential.objects.create(
            platform="facebook",
            platform_account=account,
            account_name="Iconic Page",
            account_id="page-1",
            is_active=True,
        )
        credential.set_tokens(access_token="token-123", refresh_token="")
        credential.save()

        with patch("marketing.services.social_connections.call_command") as mock_call_command:
            run_social_connection_sync(credential)

        mock_call_command.assert_called_once()
        args, kwargs = mock_call_command.call_args
        self.assertEqual(args[0], "marketing_sync_meta_daily")
        self.assertEqual(kwargs["account_id"], "page-1")
        self.assertEqual(kwargs["platform"], "facebook")

    def test_sync_endpoint_redirects_back_to_connections(self):
        account = SocialAccount.objects.create(
            platform="linkedin",
            external_account_id="org-99",
            display_name="Iconic LinkedIn",
            is_active=True,
        )
        credential = OAuthCredential.objects.create(
            platform="linkedin",
            platform_account=account,
            account_name="Iconic LinkedIn",
            account_id="org-99",
            is_active=True,
        )
        credential.set_tokens(access_token="token-123", refresh_token="")
        credential.save()

        with patch("marketing.views_social_connections.run_social_connection_sync", return_value="LinkedIn sync complete."):
            response = self.client.post(reverse("marketing_social_connection_sync", args=[credential.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("marketing_connect"), response.url)
