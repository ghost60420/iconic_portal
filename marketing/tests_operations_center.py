from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import SystemActivityLog
from marketing.models import (
    AccountMetricDaily,
    AdAccount,
    AdCampaign,
    MarketingContentIdea,
    MarketingKeywordGeneration,
    MarketingKeywordPlan,
    MarketingTask,
    MarketingTrendEntry,
    OAuthCredential,
    SocialAccount,
)


@override_settings(MARKETING_ENABLED=True, MARKETING_SOCIAL_ENABLED=True)
class MarketingOperationsCenterTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="operations-manager", password="pass1234")
        self.manager.groups.add(Group.objects.get_or_create(name="Marketing Manager")[0])
        self.client.force_login(self.manager)

    @patch("urllib.request.urlopen")
    def test_command_dashboard_and_placeholders_load_without_external_calls(self, urlopen):
        response = self.client.get(reverse("marketing_intelligence"))

        self.assertEqual(response.status_code, 200)
        for label in (
            "Marketing Command Dashboard", "Content Due This Week", "Content Overdue",
            "Top Keyword Opportunities", "Platform and API Status", "Google Trends Unavailable",
            "Marketing Task Generator", "Marketing Reports", "Waiting for API",
        ):
            self.assertContains(response, label)
        urlopen.assert_not_called()

    def test_manual_google_trend_create(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "trend",
                "trend-trend_category": "Seasonal demand",
                "trend-country": "CA",
                "trend-product": "Hoodies",
                "trend-keyword": "winter hoodie manufacturer",
                "trend-trend_direction": "rising",
                "trend-recommended_content_idea": "Winter hoodie fabric guide",
                "trend-notes": "Manual research",
            },
        )

        self.assertEqual(response.status_code, 302)
        trend = MarketingTrendEntry.objects.get(keyword="winter hoodie manufacturer")
        self.assertEqual(trend.created_by, self.manager)
        self.assertEqual(trend.trend_direction, "rising")

    def test_task_creation_from_keyword(self):
        keyword = MarketingKeywordPlan.objects.create(keyword="task source keyword")
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "task",
                "task-title": "Write the keyword landing page",
                "task-source": f"keyword:{keyword.pk}",
                "task-assigned_to": str(self.manager.pk),
                "task-due_date": (date.today() + timedelta(days=3)).isoformat(),
                "task-priority": "high",
                "task-platform": "website",
                "task-notes": "Use the stored SEO brief.",
            },
        )

        self.assertEqual(response.status_code, 302)
        task = MarketingTask.objects.get(title="Write the keyword landing page")
        self.assertEqual(task.source_keyword, keyword)
        self.assertEqual(task.assigned_to, self.manager)
        self.assertEqual(task.created_by, self.manager)

    def test_keyword_create_and_edit(self):
        keyword = MarketingKeywordPlan.objects.create(keyword="original keyword")
        response = self.client.post(
            reverse("marketing_intelligence_keyword_edit", args=[keyword.pk]),
            {
                "keyword": "updated keyword",
                "target_country": "CA",
                "target_audience": "Startup brands",
                "product_category": "hoodies",
                "search_intent": "commercial",
                "priority": "high",
                "trend_status": "rising",
                "difficulty_estimate": "medium",
                "monthly_search_estimate": "1200",
                "competition": "medium",
                "content_type": "landing_page",
                "landing_page_suggestion": "/hoodies/",
                "suggested_article": "Hoodie buyer guide",
                "suggested_video": "Hoodie quality checks",
                "status": "approved",
                "notes": "Updated by manager",
            },
        )

        self.assertEqual(response.status_code, 302)
        keyword.refresh_from_db()
        self.assertEqual(keyword.keyword, "updated keyword")
        self.assertEqual(keyword.monthly_search_estimate, 1200)

    def test_content_create_and_edit_including_youtube_short(self):
        content = MarketingContentIdea.objects.create(title="Original content")
        response = self.client.post(
            reverse("marketing_intelligence_content_edit", args=[content.pk]),
            {
                "title": "Updated YouTube Short",
                "content_type": "youtube_short",
                "target_platform": "youtube",
                "keyword": "short video keyword",
                "audience": "Startup brands",
                "funnel_stage": "awareness",
                "priority": "medium",
                "due_date": (date.today() + timedelta(days=5)).isoformat(),
                "assigned_to": str(self.manager.pk),
                "status": "scheduled",
                "notes": "Ready for filming",
            },
        )

        self.assertEqual(response.status_code, 302)
        content.refresh_from_db()
        self.assertEqual(content.content_type, "youtube_short")
        self.assertEqual(content.status, "scheduled")

    def test_ai_idea_generator_saves_all_phase3_groups(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "keyword_generation",
                "keyword_generation-country": "CA",
                "keyword_generation-industry": "Garment manufacturing",
                "keyword_generation-product": "Activewear",
                "keyword_generation-target_customer": "Fitness brands",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = MarketingKeywordGeneration.objects.get()
        for field in ("blog_ideas", "video_ideas", "social_post_ideas", "google_business_post_ideas", "email_campaign_ideas"):
            self.assertTrue(getattr(record, field), field)

    def test_all_six_reports_load_from_stored_data(self):
        for report_type in ("weekly", "monthly", "keywords", "content", "platforms", "competitors"):
            with self.subTest(report=report_type):
                response = self.client.get(reverse("marketing_intelligence_report", args=[report_type]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "No external API was called")

    def test_marketing_staff_can_create_but_cannot_edit_existing_records(self):
        staff = get_user_model().objects.create_user(username="operations-staff")
        staff.groups.add(Group.objects.get_or_create(name="Marketing Staff")[0])
        self.client.force_login(staff)
        keyword = MarketingKeywordPlan.objects.create(keyword="manager-only edit")

        create = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "trend",
                "trend-trend_category": "Manual",
                "trend-country": "US",
                "trend-product": "Uniforms",
                "trend-keyword": "uniform supplier",
                "trend-trend_direction": "stable",
                "trend-recommended_content_idea": "Uniform guide",
                "trend-notes": "",
            },
        )
        edit = self.client.get(reverse("marketing_intelligence_keyword_edit", args=[keyword.pk]))

        self.assertEqual(create.status_code, 302)
        self.assertEqual(edit.status_code, 403)

    def test_read_only_and_sales_permissions(self):
        read_only = get_user_model().objects.create_user(username="operations-read-only")
        read_only.groups.add(Group.objects.get_or_create(name="Read only Marketing")[0])
        self.client.force_login(read_only)
        self.assertEqual(self.client.get(reverse("marketing_intelligence")).status_code, 200)
        self.assertEqual(self.client.post(reverse("marketing_intelligence"), {"form_name": "trend"}).status_code, 403)

        sales = get_user_model().objects.create_user(username="operations-sales")
        sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.client.force_login(sales)
        self.assertEqual(self.client.get(reverse("marketing_intelligence")).status_code, 403)

    def test_existing_social_connection_page_still_loads(self):
        response = self.client.get(reverse("marketing_connection_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Facebook Pages")
        self.assertContains(response, "LinkedIn Company Pages")

    @override_settings(
        MARKETING_GOOGLE_CLIENT_ID="google-client",
        MARKETING_GOOGLE_CLIENT_SECRET="google-secret",
        MARKETING_GOOGLE_REDIRECT_URI="https://femline.ca/api/auth/google/callback/",
        MARKETING_META_APP_ID="meta-app",
        MARKETING_META_APP_SECRET="meta-secret",
        MARKETING_META_REDIRECT_URI="https://femline.ca/api/auth/meta/callback/",
        MARKETING_LINKEDIN_CLIENT_ID="linkedin-client",
        MARKETING_LINKEDIN_CLIENT_SECRET="linkedin-secret",
        MARKETING_LINKEDIN_REDIRECT_URI="https://femline.ca/api/auth/linkedin/callback/",
    )
    def test_marketing_operations_page_renders_without_exposing_logs_to_manager(self):
        meta = OAuthCredential.objects.create(platform="meta", account_name="Meta", is_active=True)
        meta.set_tokens(access_token="meta-token")
        meta.save()
        google = OAuthCredential.objects.create(
            platform="google",
            account_name="Google",
            is_active=True,
            last_sync_status="error",
            last_error="SERVICE_DISABLED: Google My Business API disabled",
        )
        google.set_tokens(access_token="google-token", refresh_token="refresh")
        google.save()
        linkedin = OAuthCredential.objects.create(platform="linkedin", account_name="LinkedIn", is_active=True)
        linkedin.set_tokens(access_token="linkedin-token", refresh_token="linkedin-refresh")
        linkedin.scopes = "openid,email,profile"
        linkedin.save()
        SystemActivityLog.objects.create(
            area="marketing",
            action="marketing_sync_failure",
            level="error",
            message="hidden failure log",
        )

        response = self.client.get(reverse("marketing_operations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marketing Operations")
        self.assertContains(response, "Connected")
        self.assertContains(response, "Partially Connected")
        self.assertContains(response, "Waiting Approval")
        self.assertContains(response, "Not Configured")
        self.assertContains(response, "API Blocked")
        self.assertContains(response, "Sync logs are restricted to CEO users.")
        self.assertNotContains(response, "hidden failure log")

    def test_marketing_operations_logs_visible_to_ceo_only(self):
        ceo = get_user_model().objects.create_user(username="ceo-user")
        ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        self.client.force_login(ceo)
        SystemActivityLog.objects.create(
            area="marketing",
            action="marketing_manual_sync",
            level="info",
            message="CEO visible sync log",
        )

        response = self.client.get(reverse("marketing_operations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CEO visible sync log")

    @patch("marketing.services.operations.call_command")
    def test_marketing_operations_manual_sync_uses_existing_command(self, call_command_mock):
        response = self.client.post(reverse("marketing_operations_sync", args=["ga4"]))

        self.assertEqual(response.status_code, 302)
        call_command_mock.assert_called_once()
        self.assertEqual(call_command_mock.call_args.args[0], "marketing_sync_ga4_daily")
        self.assertTrue(SystemActivityLog.objects.filter(action="marketing_manual_sync", level="info").exists())

    @override_settings(
        MARKETING_GOOGLE_CLIENT_ID="google-client",
        MARKETING_GOOGLE_CLIENT_SECRET="google-secret",
        MARKETING_GOOGLE_REDIRECT_URI="https://femline.ca/api/auth/google/callback/",
    )
    def test_google_business_partial_status_is_explicit(self):
        credential = OAuthCredential.objects.create(platform="google", account_name="Google", is_active=True)
        credential.set_tokens(access_token="token", refresh_token="refresh", expires_at=timezone.now() + timedelta(hours=1))
        credential.save()
        account = SocialAccount.objects.create(
            platform="google_business",
            external_account_id="locations/1",
            display_name="Iconic",
            is_active=True,
            last_successful_sync=timezone.now(),
            last_sync_status="ok",
            last_sync_message="Google My Business API has not been used in project 123 before or it is disabled.",
        )
        AccountMetricDaily.objects.create(account=account, date=date.today(), impressions=12, clicks=3)

        response = self.client.get(reverse("marketing_operations"))

        self.assertContains(response, "Google Business Profile")
        self.assertContains(response, "Profile Connected")
        self.assertContains(response, "Analytics Working")
        self.assertContains(response, "Reviews")
        self.assertContains(response, "Posts")
        self.assertContains(response, "Unavailable")

    @override_settings(
        MARKETING_META_APP_ID="meta-app",
        MARKETING_META_APP_SECRET="meta-secret",
        MARKETING_META_REDIRECT_URI="https://femline.ca/api/auth/meta/callback/",
    )
    def test_meta_ads_no_recent_activity_is_not_error(self):
        credential = OAuthCredential.objects.create(platform="meta", account_name="Meta", is_active=True)
        credential.set_tokens(access_token="token")
        credential.last_sync_status = "ok"
        credential.last_synced_at = timezone.now()
        credential.save()
        account = SocialAccount.objects.create(platform="meta_business", external_account_id="act_1", display_name="Ads")
        ad_account = AdAccount.objects.create(platform_account=account, external_ad_account_id="act_1", is_active=True)
        AdCampaign.objects.create(
            ad_account=ad_account,
            external_campaign_id="campaign-1",
            name="Traffic Campaign",
            status="ACTIVE",
        )

        response = self.client.get(reverse("marketing_operations"))

        self.assertContains(response, "Meta Ads")
        self.assertContains(response, "No Recent Ad Activity")
        self.assertNotContains(response, "Meta Ads sync failed")
