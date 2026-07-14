from datetime import date
from decimal import Decimal
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import Opportunity
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


class MarketingDashboardKpiTests(TestCase):
    def test_ad_spend_total_defaults_to_zero(self):
        from marketing.views import _ad_spend_total

        self.assertEqual(_ad_spend_total(date(2026, 6, 1), date(2026, 6, 30)), Decimal("0"))

    def test_executive_kpis_include_requested_labels(self):
        from marketing.views import _build_executive_kpis

        period = {"start": date(2026, 6, 1), "end": date(2026, 6, 30)}
        period_summary = {
            "current_leads": 6,
            "current_metrics": {"reach": 500},
        }
        website_summary = {"current": {"visitors": 1200}}
        google_search_summary = {"current": {"clicks": 90}}
        performance_drivers = {
            "best_platform": {
                "label": "YouTube",
                "reach": 500,
                "clicks": 12,
            }
        }
        top_posts = [{"display_title": "Best post", "engagement_score": 42}]

        with patch("marketing.views._ad_spend_total", return_value=Decimal("120")), patch(
            "marketing.views._monthly_growth_percent",
            return_value=12.3,
        ):
            rows = _build_executive_kpis(
                period,
                period_summary,
                website_summary,
                google_search_summary,
                performance_drivers,
                top_posts,
            )

        self.assertEqual(
            [row["label"] for row in rows],
            [
                "Leads Generated",
                "Website Visitors",
                "Social Reach",
                "Search Clicks",
                "Top Performing Channel",
                "Top Performing Post",
                "Cost Per Lead",
                "Monthly Growth %",
            ],
        )
        self.assertEqual(rows[6]["value"], "$20.00")
        self.assertEqual(rows[7]["value"], "+12%")


@override_settings(MARKETING_ENABLED=True, MARKETING_OUTREACH_ENABLED=True)
class MarketingPhaseD1RepairTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="marketing-admin",
            email="marketing-admin@example.com",
            password="pass1234",
        )
        self.client.force_login(self.user)

    def test_dashboard_and_campaigns_render_with_customer_origin_opportunity(self):
        Opportunity.objects.create(
            lead=None,
            stage="Closed Won",
            order_value=Decimal("1200.00"),
        )

        for url_name in ["marketing_dashboard", "marketing_campaigns"]:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "marketing_density.css")

    def test_outreach_dashboard_uses_unique_prefixed_form_ids(self):
        response = self.client.get(reverse("marketing_outreach"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertLessEqual(ids.count("id_contact_list"), 1)
        self.assertLessEqual(ids.count("id_name"), 1)
        self.assertIn("id_list-name", ids)
        self.assertIn("id_upload-contact_list", ids)
        self.assertIn("id_campaign-name", ids)


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
