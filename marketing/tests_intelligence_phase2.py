from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from marketing.integrations import integration_statuses
from marketing.models import (
    MarketingContentIdea,
    MarketingKeywordGeneration,
    MarketingKeywordPlan,
    MarketingVideoIdea,
)
from marketing.views_intelligence import marketing_intelligence


@override_settings(MARKETING_ENABLED=True)
class MarketingIntelligencePhase2Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="phase2-manager", password="pass1234")
        self.user.groups.add(Group.objects.get_or_create(name="Marketing Manager")[0])
        self.client.force_login(self.user)

    @patch("urllib.request.urlopen")
    def test_dashboard_renders_all_phase2_sections_without_network(self, urlopen):
        response = self.client.get(reverse("marketing_intelligence"))

        self.assertEqual(response.status_code, 200)
        for label in (
            "Marketing Command Dashboard", "Marketing Score", "SEO Keyword Center", "AI Idea Generator",
            "Blog Planner", "Video Planner", "Content Calendar", "Google Trends Placeholder",
            "Competitor Watch", "Marketing AI Assistant", "Future Integration Readiness",
        ):
            with self.subTest(label=label):
                self.assertContains(response, label)
        urlopen.assert_not_called()

    def test_keyword_generator_saves_every_recommendation_group(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "keyword_generation",
                "keyword_generation-country": "CA",
                "keyword_generation-industry": "Apparel manufacturing",
                "keyword_generation-product": "Private label hoodies",
                "keyword_generation-target_customer": "Startup streetwear brands",
            },
        )

        self.assertEqual(response.status_code, 302)
        generation = MarketingKeywordGeneration.objects.get()
        self.assertEqual(generation.created_by, self.user)
        for field in (
            "primary_keywords", "secondary_keywords", "long_tail_keywords", "customer_questions",
            "comparison_keywords", "buying_intent_keywords", "commercial_keywords", "local_keywords",
            "brand_keywords",
        ):
            self.assertTrue(getattr(generation, field), field)

    def test_blog_planner_saves_planning_fields_without_publishing(self):
        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "blog",
                "blog-title": "Low MOQ hoodie production guide",
                "blog-keyword": "low MOQ hoodie manufacturer",
                "blog-secondary_keywords": "startup hoodies, private label fleece",
                "blog-meta_title": "Low MOQ Hoodie Manufacturing Guide",
                "blog-meta_description": "A practical planning guide for startup brands.",
                "blog-outline": "Introduction\nFabric\nSampling\nProduction",
                "blog-call_to_action": "Request a manufacturing consultation",
                "blog-audience": "Startup brands",
                "blog-estimated_read_time": "8",
                "blog-author": str(self.user.pk),
                "blog-priority": "high",
                "blog-due_date": (date.today() + timedelta(days=5)).isoformat(),
                "blog-status": "in_progress",
                "blog-notes": "Planning only",
            },
        )

        self.assertEqual(response.status_code, 302)
        blog = MarketingContentIdea.objects.get(title="Low MOQ hoodie production guide")
        self.assertEqual(blog.content_type, "blog")
        self.assertEqual(blog.target_platform, "website")
        self.assertEqual(blog.author, self.user)
        self.assertEqual(blog.status, "in_progress")

    def test_keyword_can_generate_complete_video_plan(self):
        keyword = MarketingKeywordPlan.objects.create(
            keyword="ethical hoodie manufacturer",
            suggested_video="How ethical hoodie production works",
            product_category="hoodies",
        )

        response = self.client.post(
            reverse("marketing_intelligence"),
            {"form_name": "generate_video", "keyword_id": keyword.pk},
        )

        self.assertEqual(response.status_code, 302)
        video = MarketingVideoIdea.objects.get(target_keyword=keyword.keyword)
        self.assertEqual(video.video_title, keyword.suggested_video)
        self.assertTrue(video.thumbnail_text)
        self.assertTrue(video.opening)
        self.assertTrue(video.main_talking_points)
        self.assertTrue(video.closing_cta)

    def test_seo_search_filter_sort_and_pagination(self):
        MarketingKeywordPlan.objects.bulk_create(
            [
                MarketingKeywordPlan(
                    keyword=f"Canada hoodie opportunity {number:02}",
                    target_country="CA",
                    monthly_search_estimate=number * 100,
                )
                for number in range(31)
            ]
        )

        response = self.client.get(
            reverse("marketing_intelligence"),
            {"q": "Canada hoodie", "country": "CA", "sort": "-searches", "page": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["keyword_page"].number, 2)
        self.assertContains(response, "Page 2 of")

    def test_kanban_status_update_is_internal_only(self):
        content = MarketingContentIdea.objects.create(title="Move this content", status="idea")

        response = self.client.post(
            reverse("marketing_intelligence"),
            {"form_name": "status_update", "item_type": "content", "item_id": content.pk, "status": "scheduled"},
        )

        self.assertEqual(response.status_code, 200)
        content.refresh_from_db()
        self.assertEqual(content.status, "scheduled")

    def test_read_only_marketing_can_view_but_cannot_write(self):
        user = get_user_model().objects.create_user(username="view-read-only-marketing")
        user.groups.add(Group.objects.get_or_create(name="Read only Marketing")[0])
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("marketing_intelligence")).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("marketing_intelligence"), {"form_name": "keyword"}).status_code,
            403,
        )

    def test_sales_without_explicit_marketing_role_is_blocked(self):
        user = get_user_model().objects.create_user(username="phase2-sales")
        user.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("marketing_intelligence")).status_code, 403)

    def test_marketing_staff_can_create(self):
        staff = get_user_model().objects.create_user(username="marketing-staff")
        staff.groups.add(Group.objects.get_or_create(name="Marketing Staff")[0])
        self.client.force_login(staff)

        response = self.client.post(
            reverse("marketing_intelligence"),
            {
                "form_name": "keyword",
                "keyword-keyword": "marketing staff keyword",
                "keyword-target_country": "CA",
                "keyword-product_category": "hoodies",
                "keyword-search_intent": "commercial",
                "keyword-priority": "medium",
                "keyword-trend_status": "unknown",
                "keyword-difficulty_estimate": "unknown",
                "keyword-content_type": "blog",
                "keyword-status": "idea",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(MarketingKeywordPlan.objects.filter(keyword="marketing staff keyword").exists())

    def test_all_future_adapters_are_safe_placeholders(self):
        statuses = integration_statuses()

        self.assertEqual(len(statuses), 13)
        self.assertTrue(all(item.status == "waiting" for item in statuses))
        self.assertTrue(all(item.message == "Waiting for API" for item in statuses))

    def test_dashboard_query_budget_and_no_n_plus_one(self):
        request = RequestFactory().get(reverse("marketing_intelligence"))
        request.user = self.user
        request.marketing_can_edit = True
        # Prime the shared CRM header/permission caches; the feature budget is measured warm.
        marketing_intelligence(request)
        with CaptureQueriesContext(connection) as baseline_queries:
            response = marketing_intelligence(request)
        self.assertEqual(response.status_code, 200)

        MarketingContentIdea.objects.bulk_create(
            [MarketingContentIdea(title=f"Scale content {number}", due_date=date.today()) for number in range(30)]
        )
        MarketingVideoIdea.objects.bulk_create(
            [MarketingVideoIdea(video_title=f"Scale video {number}", due_date=date.today()) for number in range(30)]
        )
        with CaptureQueriesContext(connection) as populated_queries:
            populated = marketing_intelligence(request)
        self.assertEqual(populated.status_code, 200)
        self.assertLessEqual(len(baseline_queries), 10)
        self.assertLessEqual(len(populated_queries), 10)
        self.assertLessEqual(len(populated_queries), len(baseline_queries))
