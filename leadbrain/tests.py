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
from leadbrain.models import LeadBrainCompany, LeadBrainUpload
from leadbrain.services.classification_service import classify_company
from leadbrain.services.file_parser import parse_uploaded_file
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
        self.user = user_model.objects.create_user(username="leadbrain", password="pass123")
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
        self.assertEqual(upload.total_rows, 1)
        self.assertEqual(upload.pending_rows, 1)
        self.assertEqual(upload.processing_rows, 0)
        self.assertEqual(upload.completed_rows, 0)
        self.assertEqual(upload.failed_rows, 0)
        self.assertEqual(upload.progress_percent, 0)
        self.assertEqual(upload.status_note, "Background batch analysis is running.")
        self.assertEqual(company.research_status, LeadBrainCompany.STATUS_PENDING)
        launch_processing.assert_called_once_with(upload.pk)

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
            "leadbrain.management.commands.process_leadbrain_uploads.research_company",
            return_value=research_payload,
        ), patch(
            "leadbrain.management.commands.process_leadbrain_uploads.classify_company",
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
