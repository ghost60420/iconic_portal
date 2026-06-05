import tempfile
from datetime import timedelta
from hashlib import sha256
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leadbrain.forms import LeadBrainUploadForm
from leadbrain.models import (
    LeadBrainCompany,
    LeadBrainDiscoveryCandidate,
    LeadBrainDiscoveryJob,
    LeadBrainDiscoveryRun,
    LeadBrainUpload,
    LeadBrainWorker,
)
from leadbrain.services.background_runner import launch_upload_processing
from leadbrain.services.classification_service import classify_company
from leadbrain.services.cleanup_service import cleanup_leadbrain_data
from leadbrain.services.discovery_service import (
    DISCOVERY_MAX_JOBS_PER_DAY,
    DISCOVERY_MAX_RESULTS,
    DISCOVERY_MIN_RESULTS,
    can_queue_discovery_job,
    process_discovery_runs,
    queue_manual_discovery_run,
    schedule_due_discovery_runs,
)
from leadbrain.services.file_parser import parse_uploaded_file, parse_uploaded_file_report
from leadbrain.services.import_service import prepare_import_rows
from leadbrain.services.lead_export import create_lead_from_company
from leadbrain.services.processing_service import claim_batch
from leadbrain.services.research_service import research_company
from leadbrain.services.upload_state import compute_uploaded_file_hash
from leadbrain.views import UPLOAD_PREVIEW_SESSION_KEY
from crm.models import Lead


class LeadBrainUploadFormTests(SimpleTestCase):
    def test_rejects_invalid_extension(self):
        form = LeadBrainUploadForm(
            files={"file": SimpleUploadedFile("companies.txt", b"bad")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Please upload a CSV, XLSX, or XLS file.", form.errors["file"])

    def test_compute_uploaded_file_hash_resets_pointer(self):
        upload = SimpleUploadedFile("companies.csv", b"Company Name\nABC Apparel\n")
        digest = compute_uploaded_file_hash(upload)
        self.assertEqual(len(digest), 64)
        self.assertEqual(upload.read(), b"Company Name\nABC Apparel\n")


class LeadBrainFileParserTests(SimpleTestCase):
    def test_parse_csv_returns_clean_rows(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as handle:
            handle.write("Company Name,Website,Email,Phone,Country,City\n")
            handle.write("ABC Apparel,abcapparel.com,info@abcapparel.com,123456,Canada,Vancouver\n")
            file_path = handle.name

        rows = parse_uploaded_file(file_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company_name"], "ABC Apparel")
        self.assertEqual(rows[0]["website"], "https://abcapparel.com")
        self.assertEqual(rows[0]["email"], "info@abcapparel.com")

    def test_parse_csv_report_tracks_source_and_blank_rows(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as handle:
            handle.write("Company Name,Website,Email\n")
            handle.write("ABC Apparel,abcapparel.com,info@abcapparel.com\n")
            handle.write(",,\n")
            file_path = handle.name

        report = parse_uploaded_file_report(file_path)
        self.assertEqual(report["source_row_count"], 1)
        self.assertEqual(report["blank_rows"], 1)
        self.assertEqual(len(report["rows"]), 1)

    def test_parse_csv_auto_detects_header_row_and_aliases(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as handle:
            handle.write("Lead Brain Export\n")
            handle.write("Generated 2026-04-17\n")
            handle.write(",,,\n")
            handle.write("Name,Site,Contact_Email,Phone\n")
            handle.write("ABC Apparel,abcapparel.com,info@abcapparel.com,123456\n")
            file_path = handle.name

        report = parse_uploaded_file_report(file_path)

        self.assertEqual(report["header_row_number"], 4)
        self.assertEqual(report["source_row_count"], 1)
        self.assertEqual(
            [column["canonical"] for column in report["detected_columns"][:3]],
            ["company_name", "website", "email"],
        )
        self.assertEqual(report["sample_rows"][0]["company_name"], "ABC Apparel")
        self.assertEqual(report["sample_rows"][0]["website"], "https://abcapparel.com")


class LeadBrainDuplicateImportTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="leadbrain-dup", password="pass123", is_staff=True)
        self.upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/existing.csv",
            file_name="existing.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )

    def test_prepare_import_rows_reports_existing_website_duplicates(self):
        LeadBrainCompany.objects.create(
            upload=self.upload,
            row_number=1,
            company_name="Outdoor Cap",
            website="http://www.outdoorcap.com",
            email="jshort@outdoorcap.com",
            raw_row_json={"Company Name": "Outdoor Cap"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )
        rows = [
            {
                "row_number": 2,
                "company_name": "Outdoor Cap",
                "website": "http://www.outdoorcap.com",
                "email": "jshort@outdoorcap.com",
                "raw_row_json": {"Company Name": "Outdoor Cap"},
            },
            {
                "row_number": 3,
                "company_name": "New Brand",
                "website": "http://www.newbrand.com",
                "email": "hello@newbrand.com",
                "raw_row_json": {"Company Name": "New Brand"},
            },
        ]

        result = prepare_import_rows(rows)

        self.assertEqual(result["imported_rows"], 1)
        self.assertEqual(result["skipped_duplicate_rows"], 1)
        self.assertIn("website exact match", result["duplicate_examples"][0])

    def test_prepare_import_rows_reports_same_file_duplicates(self):
        rows = [
            {
                "row_number": 2,
                "company_name": "Outdoor Cap",
                "website": "http://www.outdoorcap.com",
                "email": "jshort@outdoorcap.com",
                "raw_row_json": {"Company Name": "Outdoor Cap"},
            },
            {
                "row_number": 3,
                "company_name": "Outdoor Cap",
                "website": "http://www.outdoorcap.com",
                "email": "jshort@outdoorcap.com",
                "raw_row_json": {"Company Name": "Outdoor Cap"},
            },
        ]

        result = prepare_import_rows(rows)

        self.assertEqual(result["imported_rows"], 1)
        self.assertEqual(result["skipped_duplicate_rows"], 1)
        self.assertIn("same file", result["duplicate_examples"][0])


class LeadBrainDiscoveryTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="leadbrain-discovery", password="pass123", is_staff=True)
        self.client.force_login(self.user)

    def test_run_now_view_queues_background_task(self):
        job = LeadBrainDiscoveryJob.objects.create(
            created_by=self.user,
            name="Canada streetwear",
            source_type=LeadBrainDiscoveryJob.SOURCE_WEB,
            selected_sources_json=[LeadBrainDiscoveryJob.SOURCE_WEB],
            source_types_json=[LeadBrainDiscoveryJob.SOURCE_WEB],
            country=LeadBrainDiscoveryJob.COUNTRY_CANADA,
            countries_json=[LeadBrainDiscoveryJob.COUNTRY_CANADA],
            niche=LeadBrainDiscoveryJob.NICHE_STREETWEAR,
            niches_json=[LeadBrainDiscoveryJob.NICHE_STREETWEAR],
            max_results=DISCOVERY_MAX_RESULTS,
            max_results_per_run=DISCOVERY_MAX_RESULTS,
            minimum_score=65,
            min_fit_score=65,
        )
        with patch("leadbrain.views.run_discovery_job_task.delay") as delay, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("leadbrain_discovery_job_run_now", args=[job.pk]),
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(job.status, LeadBrainDiscoveryJob.STATUS_QUEUED)
        delay.assert_called_once_with(job.pk)

    def test_discovery_job_form_clamps_results_to_safe_range_and_syncs_backend_fields(self):
        response = self.client.post(
            reverse("leadbrain_discovery_job_create"),
            {
                "name": "Streetwear Canada",
                "selected_sources": [LeadBrainDiscoveryJob.SOURCE_WEB, LeadBrainDiscoveryJob.SOURCE_DIRECTORIES],
                "country": LeadBrainDiscoveryJob.COUNTRY_CANADA,
                "niche": LeadBrainDiscoveryJob.NICHE_STREETWEAR,
                "schedule_type": LeadBrainDiscoveryJob.SCHEDULE_MANUAL,
                "max_results": 5,
                "max_runs_per_day": 3,
                "apparel_only": "on",
                "minimum_score": 40,
            },
        )
        self.assertEqual(response.status_code, 302)
        job = LeadBrainDiscoveryJob.objects.get()
        self.assertEqual(job.max_results, DISCOVERY_MIN_RESULTS)
        self.assertEqual(job.max_results_per_run, DISCOVERY_MIN_RESULTS)
        self.assertEqual(job.selected_sources_json, [LeadBrainDiscoveryJob.SOURCE_WEB, LeadBrainDiscoveryJob.SOURCE_DIRECTORIES])
        self.assertEqual(job.source_types_json, [LeadBrainDiscoveryJob.SOURCE_WEB, LeadBrainDiscoveryJob.SOURCE_DIRECTORIES])
        self.assertEqual(job.countries_json, [LeadBrainDiscoveryJob.COUNTRY_CANADA])
        self.assertEqual(job.niches_json, [LeadBrainDiscoveryJob.NICHE_STREETWEAR])
        self.assertEqual(job.max_runs_per_day, DISCOVERY_MAX_JOBS_PER_DAY)
        self.assertEqual(job.minimum_score, 65)
        self.assertEqual(job.min_fit_score, 65)

    def test_can_queue_discovery_job_enforces_daily_limit(self):
        for index in range(DISCOVERY_MAX_JOBS_PER_DAY):
            job = LeadBrainDiscoveryJob.objects.create(
                created_by=self.user,
                name=f"Completed discovery {index}",
                source_type=LeadBrainDiscoveryJob.SOURCE_WEB,
                country=LeadBrainDiscoveryJob.COUNTRY_CANADA,
                niche=LeadBrainDiscoveryJob.NICHE_STREETWEAR,
                max_results=DISCOVERY_MAX_RESULTS,
                status=LeadBrainDiscoveryJob.STATUS_COMPLETE,
            )
            LeadBrainDiscoveryRun.objects.create(
                job=job,
                status=LeadBrainDiscoveryJob.STATUS_COMPLETE,
            )

        allowed, reason = can_queue_discovery_job(user=self.user)

        self.assertFalse(allowed)
        self.assertIn("Daily discovery job limit reached", reason)

    def test_schedule_due_discovery_runs_creates_single_active_run(self):
        job = LeadBrainDiscoveryJob.objects.create(
            created_by=self.user,
            source_type=LeadBrainDiscoveryJob.SOURCE_WEB,
            selected_sources_json=[LeadBrainDiscoveryJob.SOURCE_WEB],
            source_types_json=[LeadBrainDiscoveryJob.SOURCE_WEB],
            country=LeadBrainDiscoveryJob.COUNTRY_CANADA,
            countries_json=[LeadBrainDiscoveryJob.COUNTRY_CANADA],
            niche=LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR,
            niches_json=[LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR],
            schedule_type=LeadBrainDiscoveryJob.SCHEDULE_DAILY,
            max_results=DISCOVERY_MAX_RESULTS,
            max_results_per_run=DISCOVERY_MAX_RESULTS,
            minimum_score=65,
            min_fit_score=65,
            next_run_at=timezone.now() - timedelta(minutes=1),
            status=LeadBrainDiscoveryJob.STATUS_QUEUED,
        )
        runs = schedule_due_discovery_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].job_id, job.pk)
        job.refresh_from_db()
        self.assertTrue(job.next_run_at and job.next_run_at > timezone.now())
        self.assertEqual(schedule_due_discovery_runs(), [])

    def test_process_discovery_runs_saves_only_viable_candidates(self):
        job = LeadBrainDiscoveryJob.objects.create(
            created_by=self.user,
            source_type=LeadBrainDiscoveryJob.SOURCE_WEB,
            selected_sources_json=[LeadBrainDiscoveryJob.SOURCE_WEB],
            source_types_json=[LeadBrainDiscoveryJob.SOURCE_WEB],
            country=LeadBrainDiscoveryJob.COUNTRY_CANADA,
            countries_json=[LeadBrainDiscoveryJob.COUNTRY_CANADA],
            niche=LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR,
            niches_json=[LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR],
            max_results=DISCOVERY_MAX_RESULTS,
            max_results_per_run=DISCOVERY_MAX_RESULTS,
            minimum_score=65,
            min_fit_score=65,
            status=LeadBrainDiscoveryJob.STATUS_QUEUED,
        )
        LeadBrainCompany.objects.create(
            upload=LeadBrainUpload.objects.create(
                file="leadbrain/uploads/existing-discovery.csv",
                file_name="existing-discovery.csv",
                uploaded_by=self.user,
                status=LeadBrainUpload.STATUS_COMPLETE,
                is_active=True,
            ),
            row_number=1,
            company_name="Existing Discovery",
            website="https://duplicate.example.com",
            research_status=LeadBrainCompany.STATUS_COMPLETE,
            raw_row_json={"Company Name": "Existing Discovery"},
        )
        run = queue_manual_discovery_run(job, created_by=self.user)

        def fake_search_query_results(query, limit=10):
            return {
                "results": [
                    {"title": "Fresh Brand", "url": "https://freshbrand.example.com", "snippet": "Activewear brand"},
                    {"title": "Existing Discovery", "url": "https://duplicate.example.com", "snippet": "Duplicate"},
                    {"title": "Weak Brand", "url": "https://weakbrand.example.com", "snippet": "Weak"},
                ]
            }

        def fake_research(company):
            if company.company_name == "Weak Brand":
                return {
                    "website_status": "live",
                    "official_website_found": company.website,
                    "linkedin_url_found": "",
                    "instagram_url_found": "",
                    "public_email_found": "",
                    "public_phone_found": "",
                    "business_description": "Weak general business",
                    "apparel_signals": [],
                    "search_summary": "",
                    "possible_contact_name": "",
                    "possible_contact_title": "",
                    "confidence_notes": "",
                    "business_type_detected": "General Business",
                }
            return {
                "website_status": "live",
                "official_website_found": company.website,
                "linkedin_url_found": "",
                "instagram_url_found": "",
                "public_email_found": "hello@freshbrand.example.com",
                "public_phone_found": "",
                "business_description": "Activewear apparel brand",
                "apparel_signals": ["activewear", "apparel"],
                "search_summary": "Activewear apparel brand",
                "possible_contact_name": "",
                "possible_contact_title": "Buyer",
                "confidence_notes": "",
                "business_type_detected": "Apparel Brand",
            }

        def fake_classify(company, research_data):
            if company.company_name == "Weak Brand":
                return {
                    "business_type": "General Business",
                    "fit_label": LeadBrainCompany.FIT_WEAK,
                    "fit_score": 40,
                    "fit_reason": "Weak fit",
                    "ai_summary": "Weak fit",
                    "suggested_action": "Skip",
                    "best_contact_title": "",
                }
            return {
                "business_type": "Apparel Brand",
                "fit_label": LeadBrainCompany.FIT_GOOD,
                "fit_score": 84,
                "fit_reason": "Strong fit",
                "ai_summary": "Strong fit",
                "suggested_action": "Good for Custom Pitch",
                    "best_contact_title": "Buyer",
                }

        with patch(
            "leadbrain.services.discovery_service.search_query_results",
            side_effect=fake_search_query_results,
        ), patch(
            "leadbrain.services.discovery_service.research_company",
            side_effect=fake_research,
        ), patch(
            "leadbrain.services.discovery_service.classify_company",
            side_effect=fake_classify,
        ):
            processed = process_discovery_runs(limit=1, batch_size=10, run_id=run.pk)

        self.assertGreaterEqual(processed, 1)
        run.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(run.status, LeadBrainDiscoveryJob.STATUS_COMPLETE)
        self.assertEqual(run.total_candidates_saved, 1)
        self.assertGreaterEqual(run.total_duplicates_skipped, 1)
        self.assertEqual(run.total_weak_skipped, 1)
        self.assertEqual(job.candidates_saved, 1)
        self.assertGreaterEqual(job.duplicates_skipped, 1)
        self.assertEqual(job.weak_skipped, 1)
        self.assertTrue(job.upload_id)
        self.assertGreaterEqual(LeadBrainDiscoveryCandidate.objects.filter(run=run).count(), 3)
        saved_company = LeadBrainCompany.objects.get(discovery_run=run)
        self.assertEqual(saved_company.company_name, "Fresh Brand")
        self.assertEqual(saved_company.fit_score, 84)
        self.assertEqual(saved_company.discovery_job_id, job.pk)
        self.assertEqual(saved_company.source_type, LeadBrainDiscoveryJob.SOURCE_WEB)
        self.assertEqual(saved_company.raw_row_json["leadbrain_source"], "discovery")


class LeadBrainCleanupTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="leadbrain-cleanup", password="pass123", is_staff=True)
        self.client.force_login(self.user)

    def test_cleanup_archives_failed_uploads_and_duplicate_companies_by_website(self):
        failed_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/failed.csv",
            file_name="failed.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_FAILED,
        )
        keeper_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/keeper.csv",
            file_name="keeper.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
        )
        duplicate_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/duplicate.csv",
            file_name="duplicate.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
        )
        keeper = LeadBrainCompany.objects.create(
            upload=keeper_upload,
            row_number=1,
            company_name="Keep Brand",
            website="https://keepbrand.example.com",
            research_status=LeadBrainCompany.STATUS_COMPLETE,
            fit_score=88,
            moved_to_leads=True,
            raw_row_json={"Company Name": "Keep Brand"},
        )
        duplicate = LeadBrainCompany.objects.create(
            upload=duplicate_upload,
            row_number=2,
            company_name="Keep Brand Copy",
            website="http://www.keepbrand.example.com/",
            research_status=LeadBrainCompany.STATUS_PENDING,
            fit_score=12,
            raw_row_json={"Company Name": "Keep Brand Copy"},
        )

        dry_run = cleanup_leadbrain_data(apply_changes=False)
        self.assertEqual(dry_run.failed_uploads_found, 1)
        self.assertEqual(dry_run.failed_uploads_archived, 0)
        self.assertEqual(dry_run.duplicate_groups_found, 1)
        self.assertEqual(dry_run.duplicate_rows_found, 1)

        result = cleanup_leadbrain_data(apply_changes=True)

        failed_upload.refresh_from_db()
        keeper.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertEqual(result.failed_uploads_archived, 1)
        self.assertEqual(result.duplicate_rows_archived, 1)
        self.assertFalse(failed_upload.is_active)
        self.assertIn("upload status is failed", failed_upload.inactive_reason)
        self.assertTrue(keeper.is_active)
        self.assertFalse(duplicate.is_active)
        self.assertEqual(duplicate.duplicate_of_id, keeper.pk)
        self.assertIn("Duplicate website kept", duplicate.inactive_reason)

    def test_results_and_upload_history_hide_inactive_by_default(self):
        inactive_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/inactive.csv",
            file_name="inactive.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_FAILED,
            is_active=False,
            inactive_reason="Archived for cleanup.",
        )
        active_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/active.csv",
            file_name="active.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            is_active=True,
        )
        inactive_company = LeadBrainCompany.objects.create(
            upload=inactive_upload,
            row_number=1,
            company_name="Inactive Brand",
            website="https://inactive.example.com",
            is_active=False,
            inactive_reason="Archived duplicate.",
            raw_row_json={"Company Name": "Inactive Brand"},
        )
        LeadBrainCompany.objects.create(
            upload=active_upload,
            row_number=2,
            company_name="Active Brand",
            website="https://active.example.com",
            is_active=True,
            raw_row_json={"Company Name": "Active Brand"},
        )

        results_response = self.client.get(reverse("leadbrain_results"))
        uploads_response = self.client.get(reverse("leadbrain_uploads"))
        results_with_inactive = self.client.get(reverse("leadbrain_results"), {"include_inactive": "1"})
        uploads_with_inactive = self.client.get(reverse("leadbrain_uploads"), {"include_inactive": "1"})

        self.assertEqual(results_response.status_code, 200)
        self.assertEqual(uploads_response.status_code, 200)
        self.assertNotContains(results_response, inactive_company.company_name)
        self.assertNotContains(uploads_response, inactive_upload.file_name)
        self.assertContains(results_with_inactive, inactive_company.company_name)
        self.assertContains(results_with_inactive, inactive_company.inactive_reason)
        self.assertContains(uploads_with_inactive, inactive_upload.file_name)
        self.assertContains(uploads_with_inactive, inactive_upload.inactive_reason)


class LeadBrainLeadExportTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="leadbrain-export", password="pass123", is_staff=True)
        self.upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/export.csv",
            file_name="export.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )

    def test_create_lead_from_company_creates_lead_and_marks_company(self):
        company = LeadBrainCompany.objects.create(
            upload=self.upload,
            row_number=1,
            company_name="Move Brand",
            website="https://movebrand.example.com",
            email="hello@movebrand.example.com",
            phone="555-0100",
            country="Canada",
            city="Vancouver",
            linkedin_url="https://linkedin.com/company/movebrand",
            best_contact_name="Alex Buyer",
            fit_label=LeadBrainCompany.FIT_GOOD,
            fit_score=81,
            fit_reason="Strong fit",
            suggested_action="Email First",
            notes="Priority row",
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )

        result = create_lead_from_company(company)

        company.refresh_from_db()
        self.assertTrue(result.created)
        self.assertIsNotNone(result.lead)
        self.assertTrue(company.moved_to_leads)
        self.assertEqual(company.moved_to_lead_id, result.lead.pk)
        self.assertEqual(company.moved_to_lead_code, result.lead.lead_id)
        self.assertEqual(result.lead.account_brand, "Move Brand")
        self.assertEqual(result.lead.lead_type, "outbound")
        self.assertEqual(result.lead.source_channel, "Lead Brain Lite")

    def test_create_lead_from_company_blocks_duplicate_website(self):
        Lead.objects.create(
            account_brand="Existing Brand",
            website="https://existingbrand.example.com",
            company_website="https://existingbrand.example.com",
            lead_type="outbound",
            outbound_status="Not Contacted",
        )
        company = LeadBrainCompany.objects.create(
            upload=self.upload,
            row_number=2,
            company_name="Existing Brand",
            website="https://existingbrand.example.com",
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )

        result = create_lead_from_company(company)

        company.refresh_from_db()
        self.assertFalse(result.created)
        self.assertIn("website exact match", result.message)
        self.assertFalse(company.moved_to_leads)

    def test_create_lead_from_company_blocks_duplicate_email(self):
        Lead.objects.create(
            account_brand="Email Match Brand",
            email="duplicate@example.com",
            lead_type="outbound",
            outbound_status="Not Contacted",
        )
        company = LeadBrainCompany.objects.create(
            upload=self.upload,
            row_number=3,
            company_name="Email Match Brand",
            email="duplicate@example.com",
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )

        result = create_lead_from_company(company)

        company.refresh_from_db()
        self.assertFalse(result.created)
        self.assertIn("email exact match", result.message)
        self.assertFalse(company.moved_to_leads)


class LeadBrainClassificationTests(SimpleTestCase):
    def test_classify_company_scores_apparel_business(self):
        company = {
            "company_name": "ABC Apparel",
            "website": "https://abcapparel.com",
            "email": "info@abcapparel.com",
            "phone": "123456",
        }
        research = {
            "website_status": "live",
            "official_website_found": "https://abcapparel.com",
            "linkedin_url_found": "https://linkedin.com/company/abc-apparel",
            "public_email_found": "info@abcapparel.com",
            "public_phone_found": "123456",
            "business_description": "ABC Apparel is a private label clothing brand.",
            "apparel_signals": ["apparel", "private label", "clothing brand"],
            "search_summary": "Private label apparel brand with active website.",
            "possible_contact_name": "",
            "possible_contact_title": "Buyer",
            "confidence_notes": "Live website found.",
            "business_type_detected": "Manufacturer / Private Label",
        }

        result = classify_company(company, research)
        self.assertEqual(result["fit_label"], "good_fit")
        self.assertGreaterEqual(result["fit_score"], 75)
        self.assertIn(result["suggested_action"], {"Good for Custom Pitch", "Email First"})


class LeadBrainResearchTests(SimpleTestCase):
    def test_research_company_reuses_initial_website_fetch(self):
        company = type(
            "Company",
            (),
            {
                "company_name": "ABC Apparel",
                "website": "https://abcapparel.com",
            },
        )()

        with patch(
            "leadbrain.services.research_service._safe_http_get",
            return_value=(
                {
                    "url": "https://abcapparel.com",
                    "status_code": 200,
                    "content_type": "text/html",
                    "text": "<title>ABC Apparel</title><meta name='description' content='Private label apparel brand'>",
                },
                "",
            ),
        ) as safe_get, patch(
            "leadbrain.services.research_service.search_business_online",
            return_value={
                "official_website_found": "",
                "linkedin_url_found": "",
                "public_email_found": "",
                "public_phone_found": "",
                "business_description": "",
                "apparel_signals": [],
                "search_summary": "",
                "possible_contact_name": "",
                "possible_contact_title": "",
                "confidence_notes": "",
                "search_results": [],
                "business_type_detected": "",
            },
        ):
            result = research_company(company)

        self.assertEqual(safe_get.call_count, 1)
        self.assertEqual(result["website_status"], "live")
        self.assertEqual(result["official_website_found"], "https://abcapparel.com")
        self.assertEqual(result["business_description"], "Private label apparel brand")

    def test_research_company_stops_after_level_1_for_weak_row(self):
        company = type(
            "Company",
            (),
            {
                "company_name": "Northwest Holdings",
                "website": "",
                "email": "",
                "phone": "",
                "raw_row_json": {},
            },
        )()

        with patch("leadbrain.services.research_service.search_business_online") as search_business_online:
            result = research_company(company)

        search_business_online.assert_not_called()
        self.assertEqual(result["research_level_completed"], 1)
        self.assertFalse(result["level_1_passed"])
        self.assertEqual(result["research_path"], "level_1")

    def test_research_company_runs_level_2_for_promising_row(self):
        company = type(
            "Company",
            (),
            {
                "company_name": "ABC Apparel",
                "website": "https://abcapparel.com",
                "email": "",
                "phone": "",
                "raw_row_json": {},
            },
        )()
        website_status = {
            "status": "live",
            "final_url": "https://abcapparel.com",
            "status_code": 200,
            "error": "",
            "text": "<title>ABC Apparel</title><meta name='description' content='Private label apparel brand'>",
        }
        search_payload = {
            "official_website_found": "https://abcapparel.com",
            "linkedin_url_found": "https://linkedin.com/company/abc-apparel",
            "public_email_found": "info@abcapparel.com",
            "public_phone_found": "",
            "business_description": "Private label apparel brand",
            "apparel_signals": ["apparel", "private label"],
            "search_summary": "Active private label apparel company",
            "possible_contact_name": "",
            "possible_contact_title": "Buyer",
            "confidence_notes": "Public search results were used to confirm the business.",
            "search_results": [],
            "business_type_detected": "Manufacturer / Private Label",
        }

        with patch("leadbrain.services.research_service.check_website_status", return_value=website_status), patch(
            "leadbrain.services.research_service.search_business_online",
            return_value=search_payload,
        ) as search_business_online:
            result = research_company(company)

        search_business_online.assert_called_once()
        self.assertTrue(result["level_1_passed"])
        self.assertTrue(result["level_2_passed"])
        self.assertGreaterEqual(result["research_level_completed"], 2)

    def test_research_company_runs_level_3_for_strong_row(self):
        company = type(
            "Company",
            (),
            {
                "company_name": "ABC Apparel",
                "website": "https://abcapparel.com",
                "email": "",
                "phone": "",
                "raw_row_json": {},
            },
        )()
        website_status = {
            "status": "live",
            "final_url": "https://abcapparel.com",
            "status_code": 200,
            "error": "",
            "text": "<title>ABC Apparel</title><meta name='description' content='Private label apparel brand'>",
        }
        search_payload = {
            "official_website_found": "https://abcapparel.com",
            "linkedin_url_found": "https://linkedin.com/company/abc-apparel",
            "public_email_found": "info@abcapparel.com",
            "public_phone_found": "123456",
            "business_description": "Private label apparel brand with sampling and production support.",
            "apparel_signals": ["apparel", "private label", "clothing brand"],
            "search_summary": "Strong custom apparel brand with active public presence.",
            "possible_contact_name": "Sam",
            "possible_contact_title": "Buyer",
            "confidence_notes": "Public search results were used to confirm the business.",
            "search_results": [],
            "business_type_detected": "Manufacturer / Private Label",
        }

        with patch("leadbrain.services.research_service.check_website_status", return_value=website_status), patch(
            "leadbrain.services.research_service.search_business_online",
            return_value=search_payload,
        ):
            result = research_company(company)

        self.assertEqual(result["research_level_completed"], 3)
        self.assertEqual(result["research_path"], "level_3")
        self.assertTrue(result["pitch_summary"])
        self.assertTrue(result["outreach_highlights"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class LeadBrainUploadFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="leadbrain",
            password="pass123",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def _preview_token(self):
        return self.client.session[UPLOAD_PREVIEW_SESSION_KEY]["token"]

    def test_upload_saves_file_and_queues_background_parsing(self):
        upload_file = SimpleUploadedFile("companies.csv", b"Company Name\nABC Apparel\n")

        with patch("leadbrain.views.queue_parse_upload") as queue_parse_upload, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})

        self.assertEqual(response.status_code, 302)
        upload = LeadBrainUpload.objects.get()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_QUEUED)
        self.assertEqual(upload.file_name, "companies.csv")
        self.assertEqual(upload.status_note, "Upload queued for background parsing.")
        self.assertEqual(upload.companies.count(), 0)
        queue_parse_upload.assert_called_once_with(upload.pk)

    def test_upload_skips_duplicate_rows_and_invalid_rows(self):
        existing_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/existing.csv",
            file_name="existing.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )
        LeadBrainCompany.objects.create(
            upload=existing_upload,
            row_number=1,
            company_name="Existing Apparel",
            website="https://existing.example.com",
            email="existing@example.com",
            raw_row_json={"Company Name": "Existing Apparel"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )
        rows = [
            {
                "row_number": 1,
                "company_name": "ABC Apparel",
                "website": "https://abc.example.com",
                "email": "hello@abc.example.com",
                "phone": "",
                "country": "Canada",
                "city": "Vancouver",
                "raw_row_json": {"Company Name": "ABC Apparel"},
            },
            {
                "row_number": 2,
                "company_name": "ABC Apparel",
                "website": "https://abc.example.com",
                "email": "hello@abc.example.com",
                "phone": "",
                "country": "Canada",
                "city": "Vancouver",
                "raw_row_json": {"Company Name": "ABC Apparel"},
            },
            {
                "row_number": 3,
                "company_name": "Existing Apparel",
                "website": "https://existing.example.com",
                "email": "existing@example.com",
                "phone": "",
                "country": "Canada",
                "city": "Toronto",
                "raw_row_json": {"Company Name": "Existing Apparel"},
            },
            {
                "row_number": 4,
                "company_name": "",
                "website": "",
                "email": "",
                "phone": "",
                "country": "",
                "city": "",
                "raw_row_json": {},
            },
        ]
        upload_file = SimpleUploadedFile("companies.csv", b"Company Name\nABC Apparel\n")

        with patch("leadbrain.views.queue_parse_upload") as queue_parse_upload, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})

        self.assertEqual(response.status_code, 302)
        new_upload = LeadBrainUpload.objects.exclude(pk=existing_upload.pk).get()
        self.assertEqual(new_upload.status, LeadBrainUpload.STATUS_QUEUED)
        self.assertEqual(new_upload.companies.count(), 0)
        queue_parse_upload.assert_called_once_with(new_upload.pk)

    def test_upload_reuses_existing_active_job_for_duplicate_file(self):
        file_bytes = b"Company Name\nABC Apparel\n"
        file_hash = sha256(file_bytes).hexdigest()
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/companies.csv",
            file_name="companies.csv",
            file_hash=file_hash,
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PROCESSING,
            row_count=1,
            total_rows=1,
            pending_rows=1,
        )

        with patch("leadbrain.views.queue_parse_upload") as queue_parse_upload:
            response = self.client.post(
                reverse("leadbrain_upload"),
                {"file": SimpleUploadedFile("companies.csv", file_bytes)},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(LeadBrainUpload.objects.count(), 1)
        self.assertRedirects(response, f"{reverse('leadbrain_results')}?upload={upload.pk}")
        queue_parse_upload.assert_not_called()

    def test_upload_accepts_file_and_redirects_to_results(self):
        upload_file = SimpleUploadedFile("companies.csv", b"Name,Site,Contact_Email\nABC Apparel,abc.example.com,hello@abc.example.com\n")

        with patch("leadbrain.views.queue_parse_upload") as queue_parse_upload, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})

        self.assertEqual(response.status_code, 302)
        upload = LeadBrainUpload.objects.get()
        self.assertRedirects(response, f"{reverse('leadbrain_results')}?upload={upload.pk}")
        queue_parse_upload.assert_called_once_with(upload.pk)

    def test_start_analysis_launches_background_processing(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PENDING,
            row_count=1,
            total_rows=1,
            pending_rows=1,
        )
        LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="ABC Apparel",
            website="https://abcapparel.com",
            raw_row_json={"Company Name": "ABC Apparel"},
            research_status=LeadBrainCompany.STATUS_PENDING,
        )

        with patch("leadbrain.views.launch_upload_processing") as launch_processing, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("leadbrain_start_analysis", args=[upload.pk]))

        self.assertEqual(response.status_code, 302)
        upload.refresh_from_db()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_PROCESSING)
        self.assertEqual(upload.status_note, "Background research and scoring are running.")
        launch_processing.assert_called_once_with(upload.pk)

    def test_delete_upload_view_deletes_upload_and_companies(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/delete.csv",
            file_name="delete.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )
        LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="Delete Apparel",
            raw_row_json={"Company Name": "Delete Apparel"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )

        response = self.client.post(reverse("leadbrain_upload_delete", args=[upload.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(LeadBrainUpload.objects.filter(pk=upload.pk).exists())
        self.assertFalse(LeadBrainCompany.objects.filter(upload_id=upload.pk).exists())

    def test_delete_upload_view_blocks_active_processing_upload(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/processing.csv",
            file_name="processing.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PROCESSING,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            pending_rows=1,
        )
        LeadBrainWorker.objects.create(
            name="default",
            status=LeadBrainWorker.STATUS_RUNNING,
            heartbeat_at=timezone.now(),
            started_at=timezone.now(),
            current_upload=upload,
        )

        response = self.client.post(reverse("leadbrain_upload_delete", args=[upload.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(LeadBrainUpload.objects.filter(pk=upload.pk).exists())

    def test_company_delete_view_deletes_single_row_and_redirects_back(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/results.csv",
            file_name="results.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=2,
            source_row_count=2,
            total_rows=2,
            imported_rows=2,
            completed_rows=2,
            progress_percent=100,
        )
        company_one = LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="Delete Me",
            website="https://delete.example.com",
            raw_row_json={"Company Name": "Delete Me"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )
        LeadBrainCompany.objects.create(
            upload=upload,
            row_number=2,
            company_name="Keep Me",
            website="https://keep.example.com",
            raw_row_json={"Company Name": "Keep Me"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )

        response = self.client.post(
            reverse("leadbrain_company_delete", args=[company_one.pk]),
            {"next": f"{reverse('leadbrain_results')}?upload={upload.pk}"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, f"{reverse('leadbrain_results')}?upload={upload.pk}")
        self.assertFalse(LeadBrainCompany.objects.filter(pk=company_one.pk).exists())
        upload.refresh_from_db()
        self.assertEqual(upload.total_rows, 1)

    def test_company_mark_not_relevant_sets_weak_fit_and_reviewed(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/results.csv",
            file_name="results.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )
        company = LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="Weak Candidate",
            website="https://weak.example.com",
            fit_label=LeadBrainCompany.FIT_GOOD,
            fit_score=84,
            suggested_action="Good for Custom Pitch",
            raw_row_json={"Company Name": "Weak Candidate"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )

        response = self.client.post(
            reverse("leadbrain_company_mark_not_relevant", args=[company.pk]),
            {"next": reverse("leadbrain_results")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("leadbrain_results"))
        company.refresh_from_db()
        self.assertEqual(company.fit_label, LeadBrainCompany.FIT_WEAK)
        self.assertEqual(company.fit_score, 0)
        self.assertEqual(company.suggested_action, "Not Relevant")
        self.assertTrue(company.reviewed)

    def test_background_command_processes_pending_rows_in_batches(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PROCESSING,
            row_count=2,
            total_rows=2,
            pending_rows=2,
        )
        company = LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="ABC Apparel",
            website="https://abcapparel.com",
            email="",
            raw_row_json={"Company Name": "ABC Apparel"},
            research_status=LeadBrainCompany.STATUS_PENDING,
        )
        company_two = LeadBrainCompany.objects.create(
            upload=upload,
            row_number=2,
            company_name="XYZ Manufacturing",
            website="https://xyz.example.com",
            email="",
            raw_row_json={"Company Name": "XYZ Manufacturing"},
            research_status=LeadBrainCompany.STATUS_PENDING,
        )
        research_payload = {
            "website_status": "live",
            "official_website_found": "https://abcapparel.com",
            "linkedin_url_found": "https://linkedin.com/company/abc-apparel",
            "public_email_found": "info@abcapparel.com",
            "public_phone_found": "123456",
            "business_description": "Private label apparel brand",
            "apparel_signals": ["apparel", "private label"],
            "search_summary": "Active apparel company",
            "possible_contact_name": "Sam",
            "possible_contact_title": "Buyer",
            "confidence_notes": "Live website found.",
        }
        classification_payload = {
            "business_type": "Manufacturer / Private Label",
            "fit_label": LeadBrainCompany.FIT_GOOD,
            "fit_score": 84,
            "fit_reason": "Strong apparel signals.",
            "ai_summary": "Good outreach target.",
            "suggested_action": "Good for Custom Pitch",
            "best_contact_title": "Buyer",
        }

        with patch("leadbrain.management.commands.process_leadbrain_uploads.launch_upload_processing") as launch_processing:
            call_command("process_leadbrain_uploads", upload=upload.pk)

        launch_processing.assert_called_once_with(upload.pk)
        upload.refresh_from_db()
        company.refresh_from_db()
        company_two.refresh_from_db()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_PROCESSING)
        self.assertEqual(company.research_status, LeadBrainCompany.STATUS_PENDING)
        self.assertEqual(company_two.research_status, LeadBrainCompany.STATUS_PENDING)

    def test_repair_command_marks_stale_upload_failed(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PROCESSING,
            row_count=1,
            total_rows=1,
            pending_rows=0,
            processing_rows=1,
        )
        company = LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="ABC Apparel",
            raw_row_json={"Company Name": "ABC Apparel"},
            research_status=LeadBrainCompany.STATUS_PROCESSING,
        )
        stale_time = timezone.now() - timedelta(minutes=120)
        LeadBrainUpload.objects.filter(pk=upload.pk).update(updated_at=stale_time)
        LeadBrainCompany.objects.filter(pk=company.pk).update(updated_at=stale_time)

        call_command("repair_leadbrain_uploads", "--apply", "--stale-minutes", "1")

        upload.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_FAILED)
        self.assertIn("Marked failed by repair_leadbrain_uploads", upload.status_note)
        self.assertEqual(company.research_status, LeadBrainCompany.STATUS_FAILED)

    def test_launch_upload_processing_queues_celery_batches(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PENDING,
            row_count=250,
            total_rows=250,
            pending_rows=250,
        )

        with patch("leadbrain.tasks.process_upload_batch_job.delay") as delay:
            fanout = launch_upload_processing(upload.pk)

        self.assertEqual(fanout, 4)
        self.assertEqual(delay.call_count, 4)
        delay.assert_any_call(upload.pk)

    def test_launch_upload_processing_queues_single_batch_for_small_upload(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PENDING,
            row_count=1,
            total_rows=1,
            pending_rows=1,
        )

        with patch("leadbrain.tasks.process_upload_batch_job.delay") as delay:
            fanout = launch_upload_processing(upload.pk)

        self.assertEqual(fanout, 1)
        delay.assert_called_once_with(upload.pk)

    def test_claim_batch_moves_second_worker_to_next_pending_rows(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/claim.csv",
            file_name="claim.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PROCESSING,
            row_count=3,
            total_rows=3,
            pending_rows=3,
        )
        companies = [
            LeadBrainCompany.objects.create(
                upload=upload,
                row_number=index,
                company_name=f"Company {index}",
                website=f"https://company{index}.example.com",
                raw_row_json={"Company Name": f"Company {index}"},
                research_status=LeadBrainCompany.STATUS_PENDING,
            )
            for index in range(1, 4)
        ]

        first_claim = claim_batch(upload, batch_size=2)
        second_claim = claim_batch(upload, batch_size=2)

        self.assertEqual(len(first_claim), 2)
        self.assertEqual(len(second_claim), 1)
        self.assertEqual(set(first_claim).intersection(second_claim), set())
        statuses = list(
            LeadBrainCompany.objects.filter(pk__in=first_claim + second_claim).values_list(
                "research_status", "research_claim_token"
            )
        )
        self.assertTrue(all(status == LeadBrainCompany.STATUS_PROCESSING for status, _ in statuses))
        self.assertTrue(all(token for _, token in statuses))

    def test_run_worker_command_processes_upload_and_tracks_worker(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PENDING,
            row_count=1,
            total_rows=1,
            pending_rows=1,
        )
        LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="ABC Apparel",
            website="https://abcapparel.com",
            raw_row_json={"Company Name": "ABC Apparel"},
            research_status=LeadBrainCompany.STATUS_PENDING,
        )
        research_payload = {
            "website_status": "live",
            "official_website_found": "https://abcapparel.com",
            "linkedin_url_found": "",
            "public_email_found": "info@abcapparel.com",
            "public_phone_found": "",
            "business_description": "Private label apparel brand",
            "apparel_signals": ["apparel"],
            "search_summary": "Active apparel company",
            "possible_contact_name": "",
            "possible_contact_title": "Buyer",
            "confidence_notes": "Live website found.",
        }
        classification_payload = {
            "business_type": "Manufacturer / Private Label",
            "fit_label": LeadBrainCompany.FIT_GOOD,
            "fit_score": 81,
            "fit_reason": "Strong apparel signals.",
            "ai_summary": "Good outreach target.",
            "suggested_action": "Good for Custom Pitch",
            "best_contact_title": "Buyer",
        }

        with patch(
            "leadbrain.services.processing_service.research_company",
            return_value=research_payload,
        ), patch(
            "leadbrain.services.processing_service.classify_company",
            return_value=classification_payload,
        ):
            call_command("run_leadbrain_worker", "--worker", "test-worker", "--once", "--batch-size", "10")

        upload.refresh_from_db()
        worker = LeadBrainWorker.objects.get(name="test-worker")
        company = LeadBrainCompany.objects.get(upload=upload)
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_COMPLETE)
        self.assertEqual(company.research_status, LeadBrainCompany.STATUS_COMPLETE)
        self.assertEqual(worker.status, LeadBrainWorker.STATUS_STOPPED)
        self.assertEqual(worker.processed_batches, 1)
        self.assertEqual(worker.processed_rows, 1)
        self.assertIsNone(worker.current_upload)

    def test_ops_view_lists_failed_uploads_and_duplicates(self):
        failed_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/failed.csv",
            file_name="failed.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_FAILED,
            row_count=2,
            total_rows=2,
            failed_rows=2,
            status_note="Background batch analysis did not complete.",
        )
        duplicate_upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/duplicate.csv",
            file_name="duplicate.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_FAILED,
            row_count=1,
            total_rows=1,
            status_note="Duplicate upload history for review. Newer upload job is #99.",
        )
        LeadBrainWorker.objects.create(
            name="default",
            status=LeadBrainWorker.STATUS_IDLE,
            heartbeat_at=timezone.now(),
            processed_batches=3,
            processed_rows=250,
        )

        response = self.client.get(reverse("leadbrain_ops"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, failed_upload.file_name)
        self.assertContains(response, duplicate_upload.file_name)
        self.assertContains(response, "Active Uploads")

    def test_reset_command_dry_run_and_apply(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/reset.csv",
            file_name="reset.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            source_row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )
        LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="Reset Apparel",
            raw_row_json={"Company Name": "Reset Apparel"},
            research_status=LeadBrainCompany.STATUS_COMPLETE,
        )
        LeadBrainWorker.objects.create(name="default", status=LeadBrainWorker.STATUS_STOPPED)

        call_command("reset_leadbrain_data")
        self.assertEqual(LeadBrainUpload.objects.count(), 1)
        self.assertEqual(LeadBrainCompany.objects.count(), 1)
        self.assertEqual(LeadBrainWorker.objects.count(), 1)

        call_command("reset_leadbrain_data", "--apply")
        self.assertEqual(LeadBrainUpload.objects.count(), 0)
        self.assertEqual(LeadBrainCompany.objects.count(), 0)
        self.assertEqual(LeadBrainWorker.objects.count(), 0)


class LeadBrainDiscoveryUiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="leadbrain-ui",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_discovery_dashboard_and_create_page_render(self):
        response = self.client.get(reverse("leadbrain_discovery_jobs"), HTTP_HOST="femline.ca")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Discovery Jobs")

        response = self.client.get(reverse("leadbrain_discovery_job_create"), HTTP_HOST="femline.ca")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Discovery Job")

    @patch("leadbrain.views.run_discovery_job_task.delay")
    def test_create_and_run_now_job(self, mocked_delay):
        response = self.client.post(
            reverse("leadbrain_discovery_job_create"),
            {
                "name": "Canada Discovery",
                "country": LeadBrainDiscoveryJob.COUNTRY_CANADA,
                "niche": LeadBrainDiscoveryJob.NICHE_STREETWEAR,
                "selected_sources": [LeadBrainDiscoveryJob.SOURCE_WEB, LeadBrainDiscoveryJob.SOURCE_SHOPIFY],
                "schedule_type": LeadBrainDiscoveryJob.SCHEDULE_DAILY,
                "max_results": 25,
                "max_runs_per_day": 2,
                "apparel_only": "on",
                "minimum_score": 70,
            },
            HTTP_HOST="femline.ca",
        )
        self.assertEqual(response.status_code, 302)
        job = LeadBrainDiscoveryJob.objects.get(name="Canada Discovery")
        self.assertEqual(job.schedule_type, LeadBrainDiscoveryJob.SCHEDULE_DAILY)
        self.assertEqual(job.selected_sources, [LeadBrainDiscoveryJob.SOURCE_WEB, LeadBrainDiscoveryJob.SOURCE_SHOPIFY])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("leadbrain_discovery_job_run_now", args=[job.pk]), HTTP_HOST="femline.ca")
        self.assertEqual(response.status_code, 302)
        mocked_delay.assert_called_once_with(job.pk)

    def test_results_page_shows_discovery_badges(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/discovery-ui.csv",
            file_name="discovery-ui.csv",
            status=LeadBrainUpload.STATUS_COMPLETE,
            row_count=1,
            total_rows=1,
            imported_rows=1,
            completed_rows=1,
            progress_percent=100,
        )
        LeadBrainCompany.objects.create(
            upload=upload,
            row_number=1,
            company_name="Discovery Brand",
            website="https://discoverybrand.example",
            fit_label=LeadBrainCompany.FIT_GOOD,
            fit_score=84,
            research_status=LeadBrainCompany.STATUS_COMPLETE,
            raw_row_json={"leadbrain_source": "discovery"},
        )

        response = self.client.get(reverse("leadbrain_results"), HTTP_HOST="femline.ca")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Discovery")
        self.assertContains(response, "Strong")
