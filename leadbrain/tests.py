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
from leadbrain.models import LeadBrainCompany, LeadBrainUpload, LeadBrainWorker
from leadbrain.services.background_runner import launch_upload_processing
from leadbrain.services.classification_service import classify_company
from leadbrain.services.file_parser import parse_uploaded_file, parse_uploaded_file_report
from leadbrain.services.processing_service import claim_batch
from leadbrain.services.research_service import research_company
from leadbrain.views import UPLOAD_PREVIEW_SESSION_KEY


class LeadBrainUploadFormTests(SimpleTestCase):
    def test_rejects_invalid_extension(self):
        form = LeadBrainUploadForm(
            files={"file": SimpleUploadedFile("companies.txt", b"bad")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Please upload a CSV, XLSX, or XLS file.", form.errors["file"])


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

    def test_upload_saves_rows_and_queues_background_processing(self):
        rows = [
            {
                "row_number": 1,
                "company_name": "ABC Apparel",
                "website": "https://abcapparel.com",
                "email": "info@abcapparel.com",
                "phone": "",
                "country": "Canada",
                "city": "Vancouver",
                "raw_row_json": {"Company Name": "ABC Apparel"},
            }
        ]
        upload_file = SimpleUploadedFile("companies.csv", b"Company Name\nABC Apparel\n")

        parse_report = {
            "rows": rows,
            "source_row_count": 1,
            "blank_rows": 0,
            "header_row_number": 1,
            "detected_columns": [{"source": "Company Name", "canonical": "company_name"}],
            "sample_rows": rows,
        }

        with patch("leadbrain.views.parse_uploaded_file_report", return_value=parse_report), patch(
            "leadbrain.views.launch_upload_processing"
        ) as launch_processing, self.captureOnCommitCallbacks(execute=True):
            preview_response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})
            response = self.client.post(reverse("leadbrain_upload"), {"preview_token": self._preview_token()})

        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(response.status_code, 302)
        upload = LeadBrainUpload.objects.get()
        company = LeadBrainCompany.objects.get()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_PROCESSING)
        self.assertEqual(upload.row_count, 1)
        self.assertEqual(upload.source_row_count, 1)
        self.assertEqual(upload.total_rows, 1)
        self.assertEqual(upload.imported_rows, 1)
        self.assertEqual(upload.skipped_duplicate_rows, 0)
        self.assertEqual(upload.invalid_rows, 0)
        self.assertEqual(upload.pending_rows, 1)
        self.assertEqual(upload.processing_rows, 0)
        self.assertEqual(upload.completed_rows, 0)
        self.assertEqual(upload.failed_rows, 0)
        self.assertEqual(upload.progress_percent, 0)
        self.assertIn("Background batch analysis is running.", upload.status_note)
        self.assertEqual(company.research_status, LeadBrainCompany.STATUS_PENDING)
        launch_processing.assert_called_once_with(upload.pk)

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

        with patch(
            "leadbrain.views.parse_uploaded_file_report",
            return_value={
                "rows": rows,
                "source_row_count": 4,
                "blank_rows": 0,
                "header_row_number": 1,
                "detected_columns": [{"source": "Company Name", "canonical": "company_name"}],
                "sample_rows": rows[:4],
            },
        ), patch("leadbrain.views.launch_upload_processing") as launch_processing, self.captureOnCommitCallbacks(
            execute=True
        ):
            preview_response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})
            response = self.client.post(reverse("leadbrain_upload"), {"preview_token": self._preview_token()})

        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(response.status_code, 302)
        new_upload = LeadBrainUpload.objects.exclude(pk=existing_upload.pk).get()
        self.assertEqual(new_upload.source_row_count, 4)
        self.assertEqual(new_upload.imported_rows, 1)
        self.assertEqual(new_upload.skipped_duplicate_rows, 2)
        self.assertEqual(new_upload.invalid_rows, 1)
        self.assertEqual(new_upload.total_rows, 1)
        self.assertEqual(new_upload.companies.count(), 1)
        self.assertIn("missing both company name and website", new_upload.status_note)
        launch_processing.assert_called_once_with(new_upload.pk)

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

        with patch("leadbrain.views.parse_uploaded_file_report") as parse_uploaded_file_report, patch(
            "leadbrain.views.launch_upload_processing"
        ) as launch_processing:
            response = self.client.post(
                reverse("leadbrain_upload"),
                {"file": SimpleUploadedFile("companies.csv", file_bytes)},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(LeadBrainUpload.objects.count(), 1)
        self.assertRedirects(response, f"{reverse('leadbrain_results')}?upload={upload.pk}")
        parse_uploaded_file_report.assert_not_called()
        launch_processing.assert_not_called()

    def test_upload_preview_shows_detected_columns_and_invalid_reasons(self):
        rows = [
            {
                "row_number": 5,
                "company_name": "ABC Apparel",
                "website": "https://abc.example.com",
                "email": "hello@abc.example.com",
                "phone": "",
                "country": "Canada",
                "city": "Vancouver",
                "raw_row_json": {"Name": "ABC Apparel"},
            },
            {
                "row_number": 6,
                "company_name": "",
                "website": "",
                "email": "",
                "phone": "",
                "country": "",
                "city": "",
                "raw_row_json": {},
            },
        ]
        upload_file = SimpleUploadedFile("companies.csv", b"Name,Site,Contact_Email\nABC Apparel,abc.example.com,hello@abc.example.com\n")

        with patch(
            "leadbrain.views.parse_uploaded_file_report",
            return_value={
                "rows": rows,
                "source_row_count": 2,
                "blank_rows": 1,
                "header_row_number": 4,
                "detected_columns": [
                    {"source": "Name", "canonical": "company_name"},
                    {"source": "Site", "canonical": "website"},
                    {"source": "Contact_Email", "canonical": "email"},
                ],
                "sample_rows": rows[:1],
            },
        ):
            response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Header row detected on line")
        self.assertContains(response, "Name")
        self.assertContains(response, "Contact_Email")
        self.assertContains(response, "missing both company name and website")

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

        with patch("leadbrain.views.launch_upload_processing") as launch_processing:
            response = self.client.post(reverse("leadbrain_start_analysis", args=[upload.pk]))

        self.assertEqual(response.status_code, 302)
        upload.refresh_from_db()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_PROCESSING)
        self.assertEqual(upload.status_note, "Background batch analysis is running.")
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

        with patch(
            "leadbrain.services.processing_service.research_company",
            return_value=research_payload,
        ), patch(
            "leadbrain.services.processing_service.classify_company",
            return_value=classification_payload,
        ):
            call_command("process_leadbrain_uploads", upload=upload.pk, limit=1, batch_size=1)

        upload.refresh_from_db()
        company.refresh_from_db()
        company_two.refresh_from_db()
        self.assertEqual(upload.status, LeadBrainUpload.STATUS_COMPLETE)
        self.assertEqual(upload.total_rows, 2)
        self.assertEqual(upload.pending_rows, 0)
        self.assertEqual(upload.processing_rows, 0)
        self.assertEqual(upload.completed_rows, 2)
        self.assertEqual(upload.failed_rows, 0)
        self.assertEqual(upload.progress_percent, 100)
        self.assertEqual(upload.status_note, "Background batch analysis finished successfully.")
        self.assertEqual(company.research_status, LeadBrainCompany.STATUS_COMPLETE)
        self.assertEqual(company_two.research_status, LeadBrainCompany.STATUS_COMPLETE)
        self.assertEqual(company.fit_label, LeadBrainCompany.FIT_GOOD)
        self.assertEqual(company.email, "info@abcapparel.com")

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

    def test_launch_upload_processing_starts_persistent_worker(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PENDING,
            row_count=250,
            total_rows=250,
            pending_rows=250,
        )

        with patch("leadbrain.services.background_runner.subprocess.Popen") as popen:
            launch_upload_processing(upload.pk)

        workers = list(LeadBrainWorker.objects.order_by("name"))
        self.assertEqual(len(workers), 3)
        self.assertEqual(popen.call_count, 3)
        self.assertEqual(workers[0].name, "parallel-1")
        self.assertEqual(workers[0].status, LeadBrainWorker.STATUS_STARTING)
        self.assertEqual(workers[0].current_upload_id, upload.pk)
        self.assertIsNone(workers[0].pid)
        command = popen.call_args_list[0][0][0]
        self.assertIn("run_leadbrain_worker", command)
        self.assertIn("parallel-1", command)

    def test_launch_upload_processing_reuses_fresh_worker(self):
        upload = LeadBrainUpload.objects.create(
            file="leadbrain/uploads/test.csv",
            file_name="test.csv",
            uploaded_by=self.user,
            status=LeadBrainUpload.STATUS_PENDING,
            row_count=1,
            total_rows=1,
            pending_rows=1,
        )
        LeadBrainWorker.objects.create(
            name="parallel-1",
            status=LeadBrainWorker.STATUS_RUNNING,
            heartbeat_at=timezone.now(),
            started_at=timezone.now(),
        )

        with patch("leadbrain.services.background_runner.subprocess.Popen") as popen:
            launch_upload_processing(upload.pk)

        popen.assert_not_called()

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
        self.assertContains(response, "Worker Status")

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
