"""Assigned production screens and updates on synthetic, isolated test records."""
from io import BytesIO
import tempfile
from unittest.mock import patch
from PIL import Image

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models import LeadComment, ProductReferenceImage, ProductionOrder, ProductionProgressPhoto
from crm.services.restricted_production import RESTRICTED_PRODUCTION_GROUP


class RestrictedProductionUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("restricted-factory", pk=16, first_name="Factory")
        cls.user.groups.add(Group.objects.get_or_create(name=RESTRICTED_PRODUCTION_GROUP)[0])
        cls.employee = get_user_model().objects.create_superuser("restricted-ui-admin", "ui@example.com", "test")
        cls.order = ProductionOrder.objects.create(
            title="CONFIDENTIAL CUSTOMER CONTACT", order_code="PO-ASSIGNED", style_name="Cotton style",
            notes="CONFIDENTIAL ORDER NOTE", assigned_production_manager=cls.user, qty_total=100,
        )
        cls.other = ProductionOrder.objects.create(title="OTHER PRIVATE ORDER", order_code="PO-OTHER", style_name="Unassigned style")
        LeadComment.objects.create(production=cls.order, content="CONFIDENTIAL HISTORICAL COST CHAT", author_user=cls.employee)

    def setUp(self):
        self.client.force_login(self.user)

    def detail_url(self, order=None):
        return reverse("production_detail", args=[(order or self.order).pk])

    def test_only_assigned_orders_and_manufacturing_fields_render(self):
        listing = self.client.get(reverse("production_list"))
        self.assertContains(listing, "PO-ASSIGNED")
        self.assertNotContains(listing, "PO-OTHER")
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cotton style")
        for secret in ("CONFIDENTIAL CUSTOMER CONTACT", "CONFIDENTIAL ORDER NOTE", "CONFIDENTIAL HISTORICAL COST CHAT"):
            self.assertNotContains(response, secret)
        self.assertNotContains(response, 'href="' + reverse("production_next_stage", args=[self.order.pk]) + '"')
        self.assertNotContains(response, 'action="' + reverse("global_search") + '"')
        self.assertNotContains(response, 'href="' + reverse("shipment_list") + '"')
        self.assertEqual({stage.stage_key for stage in response.context["stages"]},
                         set(self.order.stages.exclude(stage_key="shipping").values_list("stage_key", flat=True)))

    def test_search_never_matches_hidden_fields_or_unassigned_records(self):
        url = reverse("production_list")
        for query in ("CONFIDENTIAL CUSTOMER CONTACT", "Unassigned style"):
            response = self.client.get(url, {"q": query})
            self.assertNotContains(response, 'href="' + self.detail_url() + '"')
            self.assertNotContains(response, 'href="' + self.detail_url(self.other) + '"')
        self.assertContains(self.client.get(url, {"q": "Cotton"}), "PO-ASSIGNED")

    def test_only_explicit_order_reference_images_appear(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            png = BytesIO()
            Image.new("RGB", (2, 2), "white").save(png, format="PNG")
            assigned = ProductReferenceImage.objects.create(production_order=self.order, image=SimpleUploadedFile("assigned.png", png.getvalue()))
            other = ProductReferenceImage.objects.create(production_order=self.other, image=SimpleUploadedFile("other.png", png.getvalue()))
            response = self.client.get(self.detail_url())
            self.assertContains(response, assigned.image.url)
            self.assertNotContains(response, other.image.url)

    def test_unassigned_detail_and_writes_are_denied(self):
        self.assertEqual(self.client.get(self.detail_url(self.other)).status_code, 404)
        self.assertEqual(self.client.post(self.detail_url(self.other), {"action": "add_comment", "comment_text": "Blocked"}).status_code, 404)
        self.assertFalse(LeadComment.objects.filter(content="Blocked").exists())

    def test_assignment_revocation_blocks_an_open_page_update(self):
        self.assertEqual(self.client.get(self.detail_url()).status_code, 200)
        ProductionOrder.objects.filter(pk=self.order.pk).update(assigned_production_manager=None)
        response = self.client.post(self.detail_url(), {"action": "add_comment", "comment_text": "Revoked"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(LeadComment.objects.filter(content="Revoked").exists())

    def test_progress_notes_have_no_sales_links_and_no_mentions_are_sent(self):
        with patch("crm.views._send_chatter_mentions") as mentions:
            response = self.client.post(self.detail_url(), {
                "action": "add_comment", "comment_text": "Cutting complete", "lead": "1", "opportunity": "1",
            })
        self.assertRedirects(response, self.detail_url())
        note = LeadComment.objects.get(content="Cutting complete")
        self.assertEqual(note.production_id, self.order.pk)
        self.assertEqual(note.author_user_id, self.user.pk)
        self.assertIsNone(note.lead_id)
        self.assertIsNone(note.opportunity_id)
        mentions.assert_not_called()
        self.assertContains(self.client.get(self.detail_url()), "Cutting complete")

    def test_generic_attachments_and_unauthorized_actions_are_denied(self):
        attachment = SimpleUploadedFile("invoice.txt", b"private")
        response = self.client.post(self.detail_url(), {"action": "add_comment", "comment_text": "blocked", "attachment": attachment})
        self.assertEqual(response.status_code, 403)
        for action in ("add_actual", "add_material", "add_line", "toggle_pin_comment", "delete_actual"):
            self.assertEqual(self.client.post(self.detail_url(), {"action": action}).status_code, 403)

    def test_photo_upload_requires_decodable_image_and_supported_stage(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            invalid = SimpleUploadedFile("fake.jpg", b"<script>not a photo</script>", content_type="image/jpeg")
            self.client.post(self.detail_url(), {"action": "add_progress_photo", "progress_stage": "qc", "progress_photo": invalid})
            self.assertFalse(ProductionProgressPhoto.objects.filter(order=self.order).exists())
            # Generate a valid image rather than depending on a copied PNG CRC.
            png = BytesIO()
            Image.new("RGB", (2, 2), "white").save(png, format="PNG")
            image = SimpleUploadedFile("qc.png", png.getvalue(), content_type="image/png")
            response = self.client.post(self.detail_url(), {"action": "add_progress_photo", "progress_stage": "qc", "progress_photo": image})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(ProductionProgressPhoto.objects.filter(order=self.order, stage="qc", uploaded_by=self.user).count(), 1)

    def test_csrf_is_required_for_production_notes(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.detail_url(), {"action": "add_comment", "comment_text": "blocked"}).status_code, 403)

    def test_detail_and_search_stay_within_query_budgets_without_n_plus_one(self):
        # Warm framework/template caches before checking bounded page queries.
        self.client.get(self.detail_url())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 8, [query["sql"] for query in queries])
        for number in range(8):
            ProductionOrder.objects.create(title="More assigned", style_name="Cotton extra", assigned_production_manager=self.user)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("production_list"), {"q": "Cotton"})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 6, [query["sql"] for query in queries])
