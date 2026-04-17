import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from leadbrain.forms import LeadBrainUploadForm
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
