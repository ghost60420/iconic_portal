from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models_access import UserAccess
from marketing.models import (
    Contact,
    SeoProperty,
    SocialAccount,
    SocialContent,
    SocialMetricDaily,
    AccountMetricDaily,
    OAuthCredential,
    InsightItem,
)
from marketing.services.upsert import upsert_seo_query_daily, upsert_social_metric_daily, upsert_account_metric_daily
from marketing.ai.engine import generate_insights
from marketing.utils.outreach import can_send_to_contact


class MarketingUpsertTests(TestCase):
    def test_upsert_seo_query_daily(self):
        prop = SeoProperty.objects.create(name="Test", gsc_site_url="https://example.com")
        payload = {
            "date": date.today(),
            "query": "test",
            "page": "https://example.com",
            "country": "",
            "device": "",
            "clicks": 10,
            "impressions": 100,
            "ctr": 0.1,
            "position": 3.0,
        }
        upsert_seo_query_daily(property_obj=prop, payload=payload)
        payload["clicks"] = 15
        upsert_seo_query_daily(property_obj=prop, payload=payload)

        self.assertEqual(prop.query_days.count(), 1)
        self.assertEqual(prop.query_days.first().clicks, 15)

    def test_upsert_social_metric_daily(self):
        account = SocialAccount.objects.create(platform="tiktok", external_account_id="1", display_name="Test")
        content = SocialContent.objects.create(account=account, platform="tiktok", external_content_id="c1")
        payload = {"date": date.today(), "views": 10, "likes": 2}
        upsert_social_metric_daily(content_obj=content, payload=payload)
        payload["views"] = 20
        upsert_social_metric_daily(content_obj=content, payload=payload)

        self.assertEqual(SocialMetricDaily.objects.count(), 1)
        self.assertEqual(SocialMetricDaily.objects.first().views, 20)

    def test_upsert_account_metric_daily(self):
        account = SocialAccount.objects.create(platform="youtube", external_account_id="2", display_name="Test")
        payload = {"date": date.today(), "followers_total": 100, "followers_change": 5}
        upsert_account_metric_daily(account_obj=account, payload=payload)
        payload["followers_change"] = 8
        upsert_account_metric_daily(account_obj=account, payload=payload)

        self.assertEqual(AccountMetricDaily.objects.count(), 1)
        self.assertEqual(AccountMetricDaily.objects.first().followers_change, 8)


class OutreachSafetyTests(TestCase):
    def test_can_send_to_contact(self):
        contact = Contact.objects.create(email="test@example.com", do_not_contact=True)
        self.assertFalse(can_send_to_contact(contact))


class MarketingUnsubscribeTests(TestCase):
    def test_unsubscribe(self):
        contact = Contact.objects.create(email="unsub@example.com")
        url = reverse("marketing_unsubscribe", args=[contact.unsubscribe_token])
        resp = self.client.get(url)
        contact.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(contact.do_not_contact)


class MarketingPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="mark", password="pass1234")
        access, _ = UserAccess.objects.get_or_create(user=self.user)
        access.can_marketing = False
        access.save()

    @override_settings(MARKETING_ENABLED=True)
    def test_marketing_requires_permission(self):
        self.client.login(username="mark", password="pass1234")
        resp = self.client.get(reverse("marketing_dashboard"))
        self.assertEqual(resp.status_code, 403)


class MarketingInsightTests(TestCase):
    @override_settings(MARKETING_ENABLED=True)
    def test_generate_insights_creates_content_items(self):
        account = SocialAccount.objects.create(platform="tiktok", external_account_id="abc", display_name="Test")
        content = SocialContent.objects.create(account=account, platform="tiktok", external_content_id="c1", title="Test")
        SocialMetricDaily.objects.create(
            content=content,
            date=date.today(),
            impressions=100,
            likes=10,
            comments=2,
            shares=1,
            saves=1,
        )

        generate_insights(days=7)
        self.assertTrue(InsightItem.objects.filter(source="content").exists())


class MarketingTokenTests(TestCase):
    def test_oauth_token_encryption(self):
        cred = OAuthCredential.objects.create(platform="tiktok")
        cred.set_tokens(access_token="abc123", refresh_token="ref456")
        cred.save()
        self.assertEqual(cred.get_access_token(), "abc123")
