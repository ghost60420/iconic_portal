from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from leadbrain.models import LeadBrainCompany, LeadBrainDiscoveryCandidate, LeadBrainDiscoveryJob
from leadbrain.services.discovery_service import (
    DISCOVERY_MAX_RESULTS,
    _build_query_plan,
    process_discovery_runs,
    queue_manual_discovery_run,
)
from leadbrain.services.shopify_directory import SHOPIFY_DIRECTORY_SOURCE, SHOPIFY_DIRECTORY_SOURCE_DETAIL


class LeadBrainShopifyDirectoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="leadbrain-shopify",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_create_page_shows_shopify_clothing_directory_source(self):
        response = self.client.get(reverse("leadbrain_discovery_job_create"), HTTP_HOST="femline.ca")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shopify Clothing Directory")
        self.assertContains(response, "North America")

    def test_shopify_directory_job_form_clamps_to_new_limits(self):
        response = self.client.post(
            reverse("leadbrain_discovery_job_create"),
            {
                "name": "North America Shopify Hoodies",
                "selected_sources": [SHOPIFY_DIRECTORY_SOURCE],
                "country": LeadBrainDiscoveryJob.COUNTRY_NORTH_AMERICA,
                "niche": LeadBrainDiscoveryJob.NICHE_HOODIES,
                "schedule_type": LeadBrainDiscoveryJob.SCHEDULE_MANUAL,
                "max_results": 999,
                "max_runs_per_day": 1,
                "apparel_only": "on",
                "minimum_score": 60,
            },
            HTTP_HOST="femline.ca",
        )
        self.assertEqual(response.status_code, 302)
        job = LeadBrainDiscoveryJob.objects.get(name="North America Shopify Hoodies")
        self.assertEqual(job.max_results, DISCOVERY_MAX_RESULTS)
        self.assertEqual(job.max_results_per_run, DISCOVERY_MAX_RESULTS)
        self.assertEqual(job.minimum_score, 65)
        self.assertEqual(job.min_fit_score, 65)
        self.assertEqual(job.selected_sources, [SHOPIFY_DIRECTORY_SOURCE])
        self.assertEqual(job.country, LeadBrainDiscoveryJob.COUNTRY_NORTH_AMERICA)

    def test_shopify_directory_query_plan_expands_north_america(self):
        job = LeadBrainDiscoveryJob.objects.create(
            created_by=self.user,
            name="North America Activewear",
            source_type=SHOPIFY_DIRECTORY_SOURCE,
            selected_sources_json=[SHOPIFY_DIRECTORY_SOURCE],
            source_types_json=[SHOPIFY_DIRECTORY_SOURCE],
            country=LeadBrainDiscoveryJob.COUNTRY_NORTH_AMERICA,
            countries_json=[LeadBrainDiscoveryJob.COUNTRY_NORTH_AMERICA],
            niche=LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR,
            niches_json=[LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR],
            max_results=25,
            max_results_per_run=25,
            minimum_score=65,
            min_fit_score=65,
        )
        plan = _build_query_plan(job)
        queries = [item["query"] for item in plan]
        self.assertIn("site:myshopify.com clothing brand Canada", queries)
        self.assertIn("Shopify activewear brand USA", queries)

    def test_shopify_directory_run_saves_only_strong_or_possible_and_uses_discovery_source_detail(self):
        job = LeadBrainDiscoveryJob.objects.create(
            created_by=self.user,
            name="Canada Shopify Fashion",
            source_type=SHOPIFY_DIRECTORY_SOURCE,
            selected_sources_json=[SHOPIFY_DIRECTORY_SOURCE],
            source_types_json=[SHOPIFY_DIRECTORY_SOURCE],
            country=LeadBrainDiscoveryJob.COUNTRY_CANADA,
            countries_json=[LeadBrainDiscoveryJob.COUNTRY_CANADA],
            niche=LeadBrainDiscoveryJob.NICHE_FASHION,
            niches_json=[LeadBrainDiscoveryJob.NICHE_FASHION],
            max_results=25,
            max_results_per_run=25,
            minimum_score=65,
            min_fit_score=65,
        )
        run = queue_manual_discovery_run(job)

        def fake_search_query_results(query, limit=10):
            return {
                "results": [
                    {"title": "North Brand", "url": "https://northbrand.myshopify.com/collections/all"},
                    {"title": "North Brand Hoodie", "url": "https://northbrand.myshopify.com/products/heavy-hoodie"},
                    {"title": "Second Brand", "url": "https://secondbrand.example.com"},
                ],
                "error": "",
            }

        def fake_research(company):
            if "northbrand" in company.website:
                return {
                    "website_status": "live",
                    "official_website_found": "https://northbrand.myshopify.com",
                    "linkedin_url_found": "",
                    "public_email_found": "hello@northbrand.com",
                    "public_phone_found": "",
                    "business_description": "Canadian apparel brand with hoodies and t shirts",
                    "apparel_signals": ["apparel", "hoodies", "t shirts"],
                    "search_summary": "Shopify apparel storefront with product collections",
                    "possible_contact_name": "",
                    "possible_contact_title": "Buyer",
                    "confidence_notes": "Public storefront looks active.",
                    "business_type_detected": "Apparel Brand",
                    "shopify_signal_found": True,
                    "product_or_collection_found": True,
                    "north_america_signal_found": True,
                    "contact_page_found": True,
                }
            return {
                "website_status": "live",
                "official_website_found": "https://secondbrand.example.com",
                "linkedin_url_found": "",
                "public_email_found": "",
                "public_phone_found": "",
                "business_description": "",
                "apparel_signals": [],
                "search_summary": "",
                "possible_contact_name": "",
                "possible_contact_title": "",
                "confidence_notes": "Weak brand signals.",
                "business_type_detected": "General Business",
                "shopify_signal_found": False,
                "product_or_collection_found": False,
                "north_america_signal_found": False,
                "contact_page_found": False,
            }

        def fake_classify(company, research_data):
            if "northbrand" in company.website:
                return {
                    "business_type": "Apparel Brand",
                    "fit_label": LeadBrainCompany.FIT_POSSIBLE,
                    "fit_score": 74,
                    "fit_reason": "Base score from public apparel signals.",
                    "ai_summary": "Promising apparel brand.",
                    "suggested_action": "Email First",
                    "best_contact_title": "Buyer",
                }
            return {
                "business_type": "General Business",
                "fit_label": LeadBrainCompany.FIT_POSSIBLE,
                "fit_score": 54,
                "fit_reason": "Weak public signals.",
                "ai_summary": "Needs review.",
                "suggested_action": "Review Manually",
                "best_contact_title": "",
            }

        with patch("leadbrain.services.discovery_service.search_query_results", side_effect=fake_search_query_results), patch(
            "leadbrain.services.discovery_service.research_company",
            side_effect=fake_research,
        ), patch(
            "leadbrain.services.discovery_service.classify_company",
            side_effect=fake_classify,
        ):
            processed = process_discovery_runs(limit=1, batch_size=10, run_id=run.pk)

        self.assertGreaterEqual(processed, 1)
        run.refresh_from_db()
        self.assertEqual(run.status, LeadBrainDiscoveryJob.STATUS_COMPLETE)
        self.assertEqual(run.total_candidates_saved, 1)
        self.assertEqual(run.total_duplicates_skipped, 1)
        self.assertEqual(run.total_weak_skipped, 1)
        self.assertEqual(LeadBrainDiscoveryCandidate.objects.filter(run=run).count(), 3)

        saved_company = LeadBrainCompany.objects.get(discovery_run=run)
        self.assertEqual(saved_company.source_type, "discovery")
        self.assertEqual(saved_company.source_detail, SHOPIFY_DIRECTORY_SOURCE_DETAIL)
        self.assertEqual(saved_company.raw_row_json["discovery_source_type"], SHOPIFY_DIRECTORY_SOURCE)
        self.assertGreater(saved_company.fit_score, 74)
