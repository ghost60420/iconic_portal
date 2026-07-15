from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from marketing.models import (
    AccountMetricDaily,
    MarketingCompetitor,
    MarketingContentIdea,
    MarketingKeywordPlan,
    MarketingVideoIdea,
    OAuthCredential,
    SeoPageDaily,
    SeoProperty,
    SeoQueryDaily,
    SocialAccount,
    SocialContent,
    SocialMetricDaily,
    WebsitePageDaily,
    WebsiteTrafficDaily,
)
from marketing.views_intelligence import marketing_intelligence


@override_settings(MARKETING_ENABLED=True, MARKETING_SOCIAL_ENABLED=True)
class MarketingIntelligenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="marketing-manager", password="pass1234")
        self.user.groups.add(Group.objects.get_or_create(name="Marketing Manager")[0])
        self.client.login(username="marketing-manager", password="pass1234")

    @patch("urllib.request.urlopen")
    def test_intelligence_page_loads_without_external_api_calls(self, urlopen):
        response = self.client.get(reverse("marketing_intelligence"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marketing Intelligence Center")
        self.assertContains(response, "Market Signals")
        self.assertContains(response, "SEO Keyword Planner")
        self.assertContains(response, "Data Source Status")
        self.assertNotContains(response, "Google Trends Unavailable")
        self.assertNotContains(response, "Google Trends")
        urlopen.assert_not_called()

    def test_intelligence_scores_use_synced_metric_rows(self):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        previous_date = month_start - timedelta(days=1)
        property_row = SeoProperty.objects.create(name="Iconic website")
        WebsiteTrafficDaily.objects.create(
            property=property_row,
            date=today,
            channel="organic",
            visitors=120,
            sessions=150,
            engaged_sessions=90,
            conversions=4,
        )
        WebsiteTrafficDaily.objects.create(
            property=property_row,
            date=previous_date,
            channel="organic",
            visitors=80,
            sessions=100,
            engaged_sessions=40,
        )
        WebsitePageDaily.objects.create(property=property_row, date=today, page_path="/hoodies/", visitors=25)
        SeoQueryDaily.objects.create(
            property=property_row,
            date=today,
            query="private label hoodies",
            page="/hoodies/",
            clicks=12,
            impressions=100,
        )
        SeoPageDaily.objects.create(property=property_row, date=today, page="/hoodies/", clicks=12, impressions=100)
        MarketingKeywordPlan.objects.create(keyword="private label hoodies", landing_page_suggestion="/hoodies/")
        social_account = SocialAccount.objects.create(
            platform="instagram",
            external_account_id="ig-1",
            display_name="Iconic Instagram",
        )
        social_content = SocialContent.objects.create(
            account=social_account,
            platform="instagram",
            external_content_id="ig-post-1",
            title="Best hoodie post",
            published_at=timezone.now(),
        )
        SocialMetricDaily.objects.create(
            content=social_content,
            date=today,
            impressions=500,
            reach=300,
            likes=80,
            comments=10,
            shares=5,
            clicks=20,
        )
        google_business = SocialAccount.objects.create(
            platform="google_business",
            external_account_id="gbp-1",
            display_name="Iconic Apparel House",
        )
        AccountMetricDaily.objects.create(
            account=google_business,
            date=today,
            impressions=1000,
            reach=700,
            clicks=35,
            engagement_total=60,
        )

        response = self.client.get(reverse("marketing_intelligence"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content Score")
        self.assertContains(response, "Website Score")
        self.assertContains(response, "Google Business Score")
        self.assertContains(response, "Overall Marketing Health")
        self.assertContains(response, "Best hoodie post")
        self.assertContains(response, "Live Data")
        self.assertContains(response, "Partial Data")
        self.assertNotContains(response, "No GA4 visitor data available")

    def test_keyword_planner_creates_record(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "keyword",
                "keyword-keyword": "premium hoodie supplier Canada",
                "keyword-target_country": "CA",
                "keyword-target_audience": "Streetwear startups",
                "keyword-product_category": "hoodies",
                "keyword-search_intent": "commercial",
                "keyword-priority": "high",
                "keyword-trend_status": "rising",
                "keyword-difficulty_estimate": "medium",
                "keyword-content_type": "landing_page",
                "keyword-landing_page_suggestion": "/hoodie-manufacturing/",
                "keyword-status": "approved",
                "keyword-notes": "Manual research",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = MarketingKeywordPlan.objects.get(keyword="premium hoodie supplier Canada")
        self.assertEqual(record.created_by, self.user)
        self.assertEqual(record.priority, "high")

    def test_content_planner_creates_record(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "content",
                "content-title": "A practical low MOQ launch checklist",
                "content-content_type": "blog",
                "content-target_platform": "website",
                "content-keyword": "low MOQ clothing manufacturer",
                "content-audience": "Startup brands",
                "content-funnel_stage": "consideration",
                "content-priority": "high",
                "content-due_date": "2026-08-01",
                "content-assigned_to": "",
                "content-status": "approved",
                "content-notes": "Internal brief only",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = MarketingContentIdea.objects.get(title="A practical low MOQ launch checklist")
        self.assertEqual(record.created_by, self.user)
        self.assertEqual(record.target_platform, "website")

    def test_video_planner_creates_record(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "video",
                "video-video_title": "How hoodie sampling protects quality",
                "video-platform": "youtube",
                "video-hook": "The sample catches expensive mistakes before bulk production.",
                "video-main_talking_points": "Tech pack, first sample, fit review, approval.",
                "video-product_category": "hoodies",
                "video-target_keyword": "hoodie manufacturer",
                "video-status": "idea",
                "video-assigned_to": "",
                "video-due_date": "2026-08-03",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = MarketingVideoIdea.objects.get(video_title="How hoodie sampling protects quality")
        self.assertEqual(record.created_by, self.user)
        self.assertEqual(record.platform, "youtube")

    def test_competitor_planner_creates_record(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "competitor",
                "competitor-name": "Example Apparel",
                "competitor-website": "https://example.com/",
                "competitor-country": "CA",
                "competitor-category": "Private label apparel",
                "competitor-industry": "Apparel manufacturing",
                "competitor-status": "watching",
                "competitor-last_checked_at": "",
                "competitor-notes": "Manual tracking only",
                "competitor-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = MarketingCompetitor.objects.get(name="Example Apparel")
        self.assertEqual(record.country, "CA")
        self.assertEqual(record.status, "watching")

    def test_marketing_calendar_shows_due_content(self):
        due_date = date.today() + timedelta(days=7)
        MarketingContentIdea.objects.create(
            title="Scheduled LinkedIn manufacturing guide",
            content_type="linkedin_post",
            target_platform="linkedin",
            due_date=due_date,
            status="assigned",
            assigned_to=self.user,
        )

        response = self.client.get(reverse("marketing_intelligence"))

        self.assertContains(response, "Scheduled LinkedIn manufacturing guide")
        self.assertContains(response, due_date.strftime("%Y-%m-%d"))

    def test_google_business_approval_error_does_not_break_page(self):
        credential = OAuthCredential.objects.create(
            platform="google",
            account_name="owner@example.com",
            account_id="google-owner",
            scopes="openid email profile https://www.googleapis.com/auth/business.manage",
            last_sync_status="connected",
            last_error="Google API error 429: RESOURCE_EXHAUSTED quota limit value 0",
        )
        credential.set_tokens(access_token="google-access", refresh_token="google-refresh", expires_at=timezone.now() + timedelta(hours=1))
        credential.save()

        response = self.client.get(reverse("marketing_intelligence"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google Business Profile")
        self.assertContains(response, "Waiting for API")

    def test_linkedin_missing_organization_scopes_shows_approval_required(self):
        credential = OAuthCredential.objects.create(
            platform="linkedin",
            account_name="owner@example.com",
            account_id="linkedin-owner",
            scopes="openid profile email w_member_social",
        )
        credential.set_tokens(access_token="linkedin-access", refresh_token="linkedin-refresh", expires_at=timezone.now() + timedelta(days=1))
        credential.save()

        intelligence = self.client.get(reverse("marketing_intelligence"))
        connections = self.client.get(reverse("marketing_connection_settings"))

        self.assertContains(intelligence, "LinkedIn Analytics")
        self.assertContains(intelligence, "Waiting for API")
        self.assertContains(connections, "LinkedIn API approval required")
        self.assertContains(connections, "Community Management API")

    def test_existing_social_connection_cards_still_render(self):
        response = self.client.get(reverse("marketing_connection_settings"))

        self.assertEqual(response.status_code, 200)
        for label in ["Facebook Pages", "Instagram Business", "Google Business Profile", "LinkedIn Company Pages", "TikTok Business"]:
            with self.subTest(label=label):
                self.assertContains(response, label)

    def test_sales_user_without_marketing_role_is_blocked(self):
        sales = get_user_model().objects.create_user(username="restricted-sales", password="pass1234")
        sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.client.force_login(sales)

        response = self.client.get(reverse("marketing_intelligence"))

        self.assertEqual(response.status_code, 403)

    def test_intelligence_page_has_no_per_record_query_growth(self):
        request = RequestFactory().get(reverse("marketing_intelligence"))
        request.user = self.user
        with CaptureQueriesContext(connection) as baseline_queries:
            baseline = marketing_intelligence(request)
        self.assertEqual(baseline.status_code, 200)

        MarketingKeywordPlan.objects.bulk_create(
            [MarketingKeywordPlan(keyword=f"Bounded keyword {number}") for number in range(20)]
        )
        MarketingContentIdea.objects.bulk_create(
            [MarketingContentIdea(title=f"Bounded content {number}") for number in range(20)]
        )
        MarketingVideoIdea.objects.bulk_create(
            [MarketingVideoIdea(video_title=f"Bounded video {number}") for number in range(20)]
        )
        MarketingCompetitor.objects.bulk_create(
            [MarketingCompetitor(name=f"Bounded competitor {number}") for number in range(20)]
        )

        with CaptureQueriesContext(connection) as populated_queries:
            populated = marketing_intelligence(request)
        self.assertEqual(populated.status_code, 200)
        self.assertLessEqual(len(populated_queries), len(baseline_queries))
