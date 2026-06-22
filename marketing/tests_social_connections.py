from unittest.mock import patch
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import SystemActivityLog
from crm.models_access import UserAccess
from marketing.models import AccountMetricDaily, OAuthConnectionRequest, OAuthCredential, SeoProperty, SocialAccount
from marketing.services.google_business import fetch_google_business_account_metrics
from marketing.services.social_connections import run_social_connection_sync, save_social_connection
from marketing.utils.activity import log_marketing_sync_failure
from marketing.services.youtube import fetch_youtube_account_metrics
from marketing.views import _metric_totals, _platform_comparison


@override_settings(MARKETING_ENABLED=True, MARKETING_SOCIAL_ENABLED=True)
class MarketingSocialConnectionsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="social-admin", password="pass1234")
        access, _ = UserAccess.objects.get_or_create(user=self.user)
        access.can_marketing = True
        access.save()
        group, _ = Group.objects.get_or_create(name="Marketing Manager")
        self.user.groups.add(group)
        self.client.login(username="social-admin", password="pass1234")

    @override_settings(
        MARKETING_GOOGLE_CLIENT_ID="google-client",
        MARKETING_GOOGLE_CLIENT_SECRET="google-secret",
        MARKETING_GOOGLE_REDIRECT_URI="https://femline.ca/api/auth/google/callback/",
    )
    def test_social_connections_page_renders(self):
        response = self.client.get(reverse("marketing_connect"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Social Connections")
        self.assertContains(response, "Connect Facebook")
        self.assertContains(response, "Connect Instagram")
        self.assertContains(response, "Connect Meta Ads")
        self.assertContains(response, "Connect YouTube")
        self.assertContains(response, "Connect GA4")
        self.assertContains(response, "Connect Search Console")
        self.assertContains(response, "Connect Business Profile")
        self.assertContains(response, "Connect LinkedIn")
        self.assertContains(response, "Connect TikTok")
        self.assertContains(response, "Marketing Integrations")
        self.assertContains(response, "Connect Google")
        self.assertContains(response, "Connect Google Analytics 4")
        self.assertContains(response, "Test Callback")
        self.assertContains(response, "/api/auth/google/start/")
        self.assertContains(response, "Advanced Manual Setup")
        self.assertNotContains(response, "Advanced Manual Fallback")

    @override_settings(
        MARKETING_GOOGLE_CLIENT_ID="google-client",
        MARKETING_GOOGLE_CLIENT_SECRET="google-secret",
        MARKETING_GOOGLE_REDIRECT_URI="https://femline.ca/api/auth/google/callback",
        MARKETING_GOOGLE_SCOPES=["openid", "email", "https://www.googleapis.com/auth/analytics.readonly"],
    )
    def test_google_oauth_callback_test_route_reports_configuration(self):
        response = self.client.get(reverse("marketing_google_oauth_callback_test"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["callback_route"], "/api/auth/google/callback")
        self.assertEqual(payload["redirect_uri"], "https://femline.ca/api/auth/google/callback")

    @override_settings(
        MARKETING_GOOGLE_CLIENT_ID="google-client",
        MARKETING_GOOGLE_CLIENT_SECRET="google-secret",
        MARKETING_GOOGLE_REDIRECT_URI="https://femline.ca/api/auth/google/callback/",
        MARKETING_GOOGLE_SCOPES=["openid", "email", "https://www.googleapis.com/auth/analytics.readonly"],
    )
    def test_google_api_oauth_start_route_creates_request(self):
        response = self.client.get(reverse("marketing_google_oauth_start_api_slash"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])
        self.assertTrue(OAuthConnectionRequest.objects.filter(platform="google", status="initiated").exists())

    def test_connection_diagnostics_page_renders(self):
        response = self.client.get(reverse("marketing_connection_diagnostics"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform Connection Diagnostics")
        self.assertContains(response, "Google Analytics 4")
        self.assertContains(response, "YouTube")
        self.assertContains(response, "Google Business Profile")
        self.assertContains(response, "Sync Now")

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

    @override_settings(
        MARKETING_META_APP_ID="meta-client",
        MARKETING_META_APP_SECRET="meta-secret",
        MARKETING_META_REDIRECT_URI="https://example.com/marketing/oauth/meta/callback/",
        MARKETING_META_SCOPES=["pages_show_list", "ads_read"],
    )
    def test_meta_oauth_start_creates_request_and_redirects(self):
        response = self.client.get(reverse("marketing_oauth_start", args=["facebook"]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("facebook.com", response["Location"])
        self.assertTrue(OAuthConnectionRequest.objects.filter(platform="meta", status="initiated").exists())

    @override_settings(
        MARKETING_GOOGLE_CLIENT_ID="google-client",
        MARKETING_GOOGLE_CLIENT_SECRET="google-secret",
        MARKETING_GOOGLE_REDIRECT_URI="https://example.com/marketing/oauth/google/callback/",
        MARKETING_GOOGLE_SCOPES=["openid", "email", "https://www.googleapis.com/auth/youtube.readonly"],
    )
    def test_google_owned_platform_oauth_start_creates_request(self):
        response = self.client.get(reverse("marketing_oauth_start", args=["youtube"]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])
        self.assertTrue(OAuthConnectionRequest.objects.filter(platform="youtube", status="initiated").exists())

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

    def test_youtube_account_metrics_parse_channel_statistics(self):
        with patch(
            "marketing.services.youtube.google_api_request_json",
            return_value={
                "items": [
                    {
                        "statistics": {
                            "subscriberCount": "1250",
                            "viewCount": "98765",
                            "videoCount": "42",
                        }
                    }
                ]
            },
        ):
            rows = fetch_youtube_account_metrics(
                access_token="token",
                channel_id="channel-1",
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 21),
            )

        self.assertEqual(rows[0]["followers_total"], 1250)
        self.assertEqual(rows[0]["views"], 98765)
        self.assertEqual(rows[0]["engagement_total"], 42)

    def test_google_business_account_metrics_parse_performance_rows(self):
        response_payload = {
            "multiDailyMetricTimeSeries": [
                {
                    "dailyMetricTimeSeries": [
                        {
                            "dailyMetric": "WEBSITE_CLICKS",
                            "timeSeries": {
                                "datedValues": [
                                    {"date": {"year": 2026, "month": 6, "day": 20}, "value": "7"}
                                ]
                            },
                        },
                        {
                            "dailyMetric": "CALL_CLICKS",
                            "timeSeries": {
                                "datedValues": [
                                    {"date": {"year": 2026, "month": 6, "day": 20}, "value": "3"}
                                ]
                            },
                        },
                        {
                            "dailyMetric": "BUSINESS_DIRECTION_REQUESTS",
                            "timeSeries": {
                                "datedValues": [
                                    {"date": {"year": 2026, "month": 6, "day": 20}, "value": "5"}
                                ]
                            },
                        },
                        {
                            "dailyMetric": "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
                            "timeSeries": {
                                "datedValues": [
                                    {"date": {"year": 2026, "month": 6, "day": 20}, "value": "100"}
                                ]
                            },
                        },
                    ]
                }
            ]
        }
        with patch("marketing.services.google_business.google_api_request_json", return_value=response_payload):
            rows = fetch_google_business_account_metrics(
                access_token="token",
                account_id="locations/123",
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 21),
            )

        self.assertEqual(rows[0]["date"], date(2026, 6, 20))
        self.assertEqual(rows[0]["clicks"], 7)
        self.assertEqual(rows[0]["reach"], 5)
        self.assertEqual(rows[0]["engagement_total"], 3)
        self.assertEqual(rows[0]["impressions"], 100)

    def test_google_business_account_metrics_feed_dashboard_rollups(self):
        account = SocialAccount.objects.create(
            platform="google_business",
            external_account_id="locations/123",
            display_name="Iconic Business Profile",
            is_active=True,
        )
        AccountMetricDaily.objects.create(
            account=account,
            date=date(2026, 6, 20),
            impressions=100,
            reach=5,
            clicks=7,
            engagement_total=3,
        )

        totals = _metric_totals(date(2026, 6, 1), date(2026, 6, 21))
        platform_cards = {item["key"]: item for item in _platform_comparison(date(2026, 6, 1), date(2026, 6, 21))}

        self.assertEqual(totals["impressions"], 100)
        self.assertEqual(totals["reach"], 5)
        self.assertEqual(totals["clicks"], 7)
        self.assertEqual(totals["engagement_total"], 3)
        self.assertEqual(platform_cards["google_business"]["impressions"], 100)
        self.assertEqual(platform_cards["google_business"]["reach"], 5)
        self.assertEqual(platform_cards["google_business"]["clicks"], 7)
        self.assertEqual(platform_cards["google_business"]["engagement_total"], 3)

    def test_sync_failure_logger_writes_system_activity_log(self):
        log_marketing_sync_failure(
            platform="ga4",
            message="Google API error 403",
            model_label="marketing.SeoProperty",
            object_id="12",
        )

        log = SystemActivityLog.objects.get(action="marketing_sync_failure")
        self.assertEqual(log.area, "marketing")
        self.assertEqual(log.level, "error")
        self.assertIn("ga4", log.message)

    def test_google_analytics_admin_inventory_lists_accounts_and_properties(self):
        from marketing.services.google_oauth import (
            ANALYTICS_READONLY_SCOPE,
            credential_has_analytics_readonly_scope,
            list_ga4_admin_inventory,
        )

        credential = OAuthCredential.objects.create(
            platform="google",
            account_name="iconicapparelhouse@gmail.com",
            account_id="google-user-1",
            scopes=f"openid email {ANALYTICS_READONLY_SCOPE}",
            is_active=True,
        )

        def fake_request(url, **kwargs):
            if "accountSummaries" in url:
                return {
                    "accountSummaries": [
                        {
                            "account": "accounts/123",
                            "displayName": "Iconic Apparel House",
                            "propertySummaries": [
                                {
                                    "property": "properties/456",
                                    "displayName": "Iconic Web",
                                    "parent": "accounts/123",
                                    "propertyType": "PROPERTY_TYPE_ORDINARY",
                                }
                            ],
                        }
                    ]
                }
            if url.startswith("https://analyticsadmin.googleapis.com/v1beta/accounts"):
                return {
                    "accounts": [
                        {
                            "name": "accounts/123",
                            "displayName": "Iconic Apparel House",
                        }
                    ]
                }
            if url.startswith("https://analyticsadmin.googleapis.com/v1beta/properties"):
                return {
                    "properties": [
                        {
                            "name": "properties/456",
                            "parent": "accounts/123",
                            "displayName": "Iconic Web",
                            "propertyType": "PROPERTY_TYPE_ORDINARY",
                            "currencyCode": "CAD",
                            "timeZone": "America/Toronto",
                        }
                    ]
                }
            return {}

        with patch("marketing.services.google_oauth._request_json", side_effect=fake_request):
            inventory = list_ga4_admin_inventory("access-token", include_raw=True)

        self.assertTrue(credential_has_analytics_readonly_scope(credential))
        self.assertEqual(inventory["accounts"][0]["account_id"], "123")
        self.assertEqual(inventory["accounts"][0]["display_name"], "Iconic Apparel House")
        self.assertEqual(inventory["properties"][0]["property_id"], "456")
        self.assertEqual(inventory["properties"][0]["account_id"], "123")
        self.assertEqual(inventory["properties"][0]["currency_code"], "CAD")
        self.assertEqual(len(inventory["raw"]["account_summaries"]), 1)
        self.assertEqual(len(inventory["raw"]["accounts"]), 1)
        self.assertEqual(len(inventory["raw"]["properties"]), 1)

    def test_google_discovery_saves_single_ga4_property(self):
        from marketing.services.google_oauth import sync_google_properties

        credential = OAuthCredential.objects.create(
            platform="google",
            account_name="iconicapparelhouse@gmail.com",
            account_id="google-user-1",
            scopes="https://www.googleapis.com/auth/analytics.readonly",
            is_active=True,
        )
        credential.set_tokens(access_token="access-token", refresh_token="refresh-token")
        credential.save()

        ga4_properties = [
            {
                "property_id": "456",
                "display_name": "Iconic Web",
                "account_id": "123",
                "account_name": "Iconic Apparel House",
                "property_resource": "properties/456",
            }
        ]

        with patch("marketing.services.google_oauth.list_ga4_properties", return_value=ga4_properties), patch(
            "marketing.services.google_oauth.list_gsc_sites", return_value=[]
        ), patch("marketing.services.google_oauth.list_youtube_channels", return_value=[]), patch(
            "marketing.services.google_oauth.list_google_business_locations", return_value=[]
        ):
            result = sync_google_properties(credential=credential)

        prop = SeoProperty.objects.get(ga4_property_id="456")
        self.assertEqual(prop.name, "Iconic Web")
        self.assertEqual(result["ga4_count"], 1)
        self.assertEqual(result["selected_ga4_property_id"], "456")
