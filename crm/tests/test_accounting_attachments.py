import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.forms import MultipleFileField
from crm.models import AccountingAttachment, AccountingEntry, AccountingEntryAudit

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="iconic-accounting-test-media-")


def upload_file(name, content=b"test-file", content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class MultipleFileFieldTests(TestCase):
    def test_empty_upload_returns_empty_list(self):
        field = MultipleFileField(required=False)

        self.assertEqual(field.clean(None), [])
        self.assertEqual(field.clean([]), [])

    def test_single_file_upload_returns_list(self):
        field = MultipleFileField(required=False)
        uploaded = upload_file("receipt.pdf", b"%PDF-1.4", "application/pdf")

        cleaned = field.clean(uploaded)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].name, "receipt.pdf")

    def test_multiple_file_upload_returns_list(self):
        field = MultipleFileField(required=False)
        files = [
            upload_file("receipt.pdf", b"%PDF-1.4", "application/pdf"),
            upload_file("sample.jpg", b"jpg-data", "image/jpeg"),
            upload_file("proof.png", b"png-data", "image/png"),
        ]

        cleaned = field.clean(files)

        self.assertEqual([f.name for f in cleaned], ["receipt.pdf", "sample.jpg", "proof.png"])

    def test_invalid_file_raises_validation_error(self):
        field = MultipleFileField(required=False)

        with self.assertRaises(ValidationError):
            field.clean(object())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AccountingAttachmentUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="accounting-upload-admin",
            email="accounting-upload-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(self.user)

    def accounting_payload(self, description="Attachment upload regression"):
        return {
            "date": date.today().isoformat(),
            "side": "BD",
            "direction": "OUT",
            "status": "PAID",
            "main_type": "EXPENSE",
            "sub_type": "MATERIAL",
            "currency": "BDT",
            "amount_original": "1500.00",
            "rate_to_cad": "83.333333",
            "rate_to_bdt": "1.000000",
            "description": description,
            "internal_note": "Regression test note",
        }

    def test_bd_entry_single_pdf_upload_saves_entry_attachment_and_audit(self):
        response = self.client.post(
            reverse("accounting_entry_add_bd"),
            data={
                **self.accounting_payload("Single PDF upload"),
                "attachments": upload_file("receipt.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        self.assertEqual(response.status_code, 302)
        entry = AccountingEntry.objects.get(description="Single PDF upload")
        attachment = AccountingAttachment.objects.get(entry=entry)
        self.assertEqual(attachment.original_name, "receipt.pdf")
        self.assertEqual(attachment.uploaded_by, self.user)
        self.assertTrue(attachment.file.name)
        with attachment.file.open("rb") as saved:
            self.assertEqual(saved.read(), b"%PDF-1.4")
        self.assertTrue(AccountingEntryAudit.objects.filter(entry=entry, action="CREATE").exists())

    def test_bd_entry_multiple_pdf_jpg_png_upload_saves_all_files(self):
        files = [
            upload_file("receipt.pdf", b"%PDF-1.4", "application/pdf"),
            upload_file("style.jpg", b"jpg-data", "image/jpeg"),
            upload_file("proof.png", b"png-data", "image/png"),
        ]

        response = self.client.post(
            reverse("accounting_entry_add_bd"),
            data={**self.accounting_payload("Multiple file upload"), "attachments": files},
        )

        self.assertEqual(response.status_code, 302)
        entry = AccountingEntry.objects.get(description="Multiple file upload")
        self.assertEqual(entry.amount_original, Decimal("1500.00"))
        self.assertEqual(
            list(entry.attachments.order_by("original_name").values_list("original_name", flat=True)),
            ["proof.png", "receipt.pdf", "style.jpg"],
        )
        self.assertTrue(AccountingEntryAudit.objects.filter(entry=entry, action="CREATE").exists())

    def test_bd_entry_empty_upload_still_saves_entry_without_attachments(self):
        response = self.client.post(
            reverse("accounting_entry_add_bd"),
            data=self.accounting_payload("Empty upload"),
        )

        self.assertEqual(response.status_code, 302)
        entry = AccountingEntry.objects.get(description="Empty upload")
        self.assertEqual(entry.attachments.count(), 0)
        self.assertTrue(AccountingEntryAudit.objects.filter(entry=entry, action="CREATE").exists())

    def test_edit_entry_can_add_attachment_and_update_audit(self):
        entry = AccountingEntry.objects.create(
            date=date.today(),
            side="BD",
            direction="OUT",
            status="PAID",
            main_type="EXPENSE",
            sub_type="MATERIAL",
            currency="BDT",
            amount_original=Decimal("1000.00"),
            rate_to_cad=Decimal("83.333333"),
            rate_to_bdt=Decimal("1.000000"),
            description="Edit upload",
            created_by=self.user,
        )

        response = self.client.post(
            reverse("accounting_entry_edit", args=[entry.pk]),
            data={
                **self.accounting_payload("Edit upload changed"),
                "attachments": upload_file("edit-proof.png", b"png-data", "image/png"),
            },
        )

        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.description, "Edit upload changed")
        self.assertEqual(entry.attachments.count(), 1)
        self.assertEqual(entry.attachments.get().original_name, "edit-proof.png")
        self.assertTrue(AccountingEntryAudit.objects.filter(entry=entry, action="UPDATE").exists())
