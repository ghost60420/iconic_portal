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
from leadbrain.services.research_service import research_company


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
        self.assertEqual(report["source_row_count"], 2)
        self.assertEqual(report["blank_rows"], 1)
        self.assertEqual(len(report["rows"]), 1)


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
        ) as safe_get:
            result = research_company(company)

        self.assertEqual(safe_get.call_count, 1)
        self.assertEqual(result["website_status"], "live")
        self.assertEqual(result["official_website_found"], "https://abcapparel.com")
        self.assertEqual(result["business_description"], "Private label apparel brand")


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

        with patch("leadbrain.views.parse_uploaded_file", return_value=rows), patch(
            "leadbrain.views.launch_upload_processing"
        ) as launch_processing, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})

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
        self.assertEqual(upload.status_note, "Background batch analysis is running.")
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
            return_value={"rows": rows, "source_row_count": 4, "blank_rows": 0},
        ), patch("leadbrain.views.launch_upload_processing") as launch_processing, self.captureOnCommitCallbacks(
            execute=True
        ):
            response = self.client.post(reverse("leadbrain_upload"), {"file": upload_file})

        self.assertEqual(response.status_code, 302)
        new_upload = LeadBrainUpload.objects.exclude(pk=existing_upload.pk).get()
        self.assertEqual(new_upload.source_row_count, 4)
        self.assertEqual(new_upload.imported_rows, 1)
        self.assertEqual(new_upload.skipped_duplicate_rows, 2)
        self.assertEqual(new_upload.invalid_rows, 1)
        self.assertEqual(new_upload.total_rows, 1)
        self.assertEqual(new_upload.companies.count(), 1)
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

        with patch("leadbrain.views.parse_uploaded_file") as parse_uploaded_file, patch(
            "leadbrain.views.launch_upload_processing"
        ) as launch_processing:
            response = self.client.post(
                reverse("leadbrain_upload"),
                {"file": SimpleUploadedFile("companies.csv", file_bytes)},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(LeadBrainUpload.objects.count(), 1)
        self.assertRedirects(response, f"{reverse('leadbrain_results')}?upload={upload.pk}")
        parse_uploaded_file.assert_not_called()
        launch_processing.assert_not_called()

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
            row_count=1,
            total_rows=1,
            pending_rows=1,
        )

        with patch("leadbrain.services.background_runner.subprocess.Popen") as popen:
            launch_upload_processing(upload.pk)

        worker = LeadBrainWorker.objects.get(name="default")
        self.assertEqual(worker.status, LeadBrainWorker.STATUS_STARTING)
        self.assertEqual(worker.current_upload_id, upload.pk)
        self.assertIsNone(worker.pid)
        command = popen.call_args[0][0]
        self.assertIn("run_leadbrain_worker", command)

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
            name="default",
            status=LeadBrainWorker.STATUS_RUNNING,
            heartbeat_at=timezone.now(),
            started_at=timezone.now(),
        )

        with patch("leadbrain.services.background_runner.subprocess.Popen") as popen:
            launch_upload_processing(upload.pk)

        popen.assert_not_called()

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
