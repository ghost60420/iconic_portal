import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import AccountingAttachment, AccountingEntry, ExchangeRate


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="iconic-bd-accounting-test-media-")


def upload_file(name="bd-receipt.pdf", content=b"%PDF-1.4"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class BangladeshAccountingCleanupTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="bd-accounting-admin",
            email="bd-accounting-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)
        ExchangeRate.objects.create(cad_to_bdt=Decimal("83.3333"))

    def bd_payload(self, **overrides):
        payload = {
            "date": date.today().isoformat(),
            "side": "CA",
            "direction": "IN",
            "status": "PARTIAL",
            "main_type": "Office Rent",
            "sub_type": "Dhaka office",
            "currency": "CAD",
            "amount_original": "1500.00",
            "rate_to_cad": "1.000000",
            "rate_to_bdt": "83.333300",
            "description": "BD dropdown entry",
            "internal_note": "Regression test note",
        }
        payload.update(overrides)
        return payload

    def create_bd_entry(self, **overrides):
        defaults = {
            "date": date.today(),
            "side": "BD",
            "direction": "OUT",
            "status": "PAID",
            "main_type": "Office Rent",
            "sub_type": "Dhaka office",
            "currency": "BDT",
            "amount_original": Decimal("9000.00"),
            "rate_to_cad": Decimal("83.3333"),
            "rate_to_bdt": Decimal("1.0000"),
            "description": "Existing BD entry",
            "created_by": self.user,
        }
        defaults.update(overrides)
        return AccountingEntry.objects.create(**defaults)

    def test_bd_daily_account_is_report_only_without_duplicate_entry_form(self):
        self.create_bd_entry(main_type="LEGACY_TYPED_VALUE", description="Legacy typed display")

        response = self.client.get(reverse("accounting_bd_daily"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report-only summary")
        self.assertContains(response, "Add Bangladesh Accounting Entry")
        self.assertContains(response, "LEGACY_TYPED_VALUE")
        self.assertNotContains(response, "Add Bangladesh daily entry")
        self.assertNotContains(response, 'enctype="multipart/form-data" novalidate')
        self.assertNotContains(response, "Save entry")

    def test_bd_daily_post_does_not_create_duplicate_entry_path(self):
        before_count = AccountingEntry.objects.count()

        response = self.client.post(
            reverse("accounting_bd_daily"),
            data=self.bd_payload(description="Should not save from daily"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccountingEntry.objects.count(), before_count)
        self.assertFalse(AccountingEntry.objects.filter(description="Should not save from daily").exists())

    def test_add_bd_entry_saves_locked_bd_bdt_entry_with_dropdown_values(self):
        response = self.client.post(
            reverse("accounting_entry_add_bd"),
            data=self.bd_payload(
                direction="IN",
                status="PARTIAL",
                main_type="Customer Payment",
                description="Locked BD entry",
            ),
        )

        self.assertEqual(response.status_code, 302)
        entry = AccountingEntry.objects.get(description="Locked BD entry")
        self.assertEqual(entry.side, "BD")
        self.assertEqual(entry.currency, "BDT")
        self.assertEqual(entry.direction, "IN")
        self.assertEqual(entry.status, "PARTIAL")
        self.assertEqual(entry.main_type, "Customer Payment")
        self.assertEqual(entry.amount_original, Decimal("1500.00"))
        self.assertEqual(entry.rate_to_bdt, Decimal("1"))

    def test_add_bd_entry_renders_requested_flow_status_and_main_type_dropdowns(self):
        response = self.client.get(reverse("accounting_entry_add_bd"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="IN">IN</option>', html=True)
        self.assertContains(response, '<option value="OUT" selected>OUT</option>', html=True)
        for status in ["Paid", "Unpaid", "Pending", "Partial", "Cancelled"]:
            self.assertContains(response, status)
        for main_type in ["Office Rent", "Utility Bill", "Sewing Cost", "Customer Payment", "Other Expense"]:
            self.assertContains(response, main_type)

    def test_bd_entry_attachment_upload_still_works(self):
        response = self.client.post(
            reverse("accounting_entry_add_bd"),
            data={
                **self.bd_payload(description="BD attachment upload", main_type="Fabric"),
                "attachments": upload_file(),
            },
        )

        self.assertEqual(response.status_code, 302)
        entry = AccountingEntry.objects.get(description="BD attachment upload")
        attachment = AccountingAttachment.objects.get(entry=entry)
        self.assertEqual(attachment.original_name, "bd-receipt.pdf")
        self.assertEqual(attachment.uploaded_by, self.user)

    def test_bd_grid_filters_new_and_existing_main_type_values(self):
        self.create_bd_entry(main_type="Office Rent", description="Office rent row")
        self.create_bd_entry(main_type="LEGACY_TYPED_VALUE", description="Legacy row")
        self.create_bd_entry(main_type="Fabric", description="Fabric row")

        response = self.client.get(reverse("accounting_bd_grid"), {"main_type": "Office Rent"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Office rent row")
        self.assertNotContains(response, "Fabric row")
        self.assertContains(response, "LEGACY_TYPED_VALUE")

    def test_canada_accounting_create_page_keeps_existing_main_type_controls(self):
        response = self.client.get(reverse("accounting_entry_add_ca"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add Canada accounting entry")
        self.assertContains(response, '<option value="INCOME"></option>', html=True)
        self.assertNotContains(response, "Office Rent")
