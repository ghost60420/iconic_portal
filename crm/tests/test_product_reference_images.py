import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import Lead, Opportunity, ProductReferenceImage, ProductionOrder
from crm.services.product_reference_images import (
    link_reference_images_to_opportunity,
    link_reference_images_to_production,
    product_snapshot_for_opportunity,
    product_snapshot_for_production,
    reference_images_for_opportunity,
    reference_images_for_production,
    save_reference_images_for_lead,
)


MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ProductReferenceImageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def _image(self, name):
        return SimpleUploadedFile(name, b"reference-image", content_type="image/jpeg")

    def _lead(self, **overrides):
        values = {
            "account_brand": "Snapshot Brand",
            "lead_type": "outbound",
            "source": "Email Campaign",
            "product_interest": "Hoodie",
            "primary_product_type": "Streetwear",
            "product_category": "Hoodie",
            "order_quantity": "300 pcs",
        }
        values.update(overrides)
        return Lead.objects.create(**values)

    def _opportunity(self, lead, **overrides):
        values = {
            "lead": lead,
            "stage": "Prospecting",
            "product_category": "Hoodie",
            "product_type": "Streetwear",
            "moq_units": 300,
        }
        values.update(overrides)
        return Opportunity.objects.create(**values)

    def _production(self, lead, opportunity=None, **overrides):
        values = {
            "title": "Snapshot production",
            "order_code": "PO-SNAPSHOT-TEST",
            "lead": lead,
            "opportunity": opportunity,
            "qty_total": 300,
        }
        values.update(overrides)
        return ProductionOrder.objects.create(**values)

    def test_new_lead_codes_are_short_and_directional(self):
        outbound = Lead.objects.create(
            account_brand="Outbound Brand",
            lead_type="outbound",
            source="Email Campaign",
        )
        inbound = Lead.objects.create(
            account_brand="Inbound Brand",
            lead_type="inbound",
            source="Website Inquiry",
        )
        unknown = Lead.objects.create(
            account_brand="Unknown Brand",
            lead_type="",
            source="Other",
        )

        self.assertEqual(outbound.lead_id, "OUT-1001")
        self.assertEqual(inbound.lead_id, "IN-1001")
        self.assertEqual(unknown.lead_id, "LEAD-1001")

    def test_reference_images_link_from_lead_to_opportunity_and_production(self):
        lead = Lead.objects.create(
            account_brand="Test Streetwear Co",
            lead_type="outbound",
            source="Email Campaign",
        )
        save_reference_images_for_lead(
            lead,
            [
                {"slot": 1, "image": self._image("hoodie.jpg"), "caption": "Style 1 Hoodie"},
                {"slot": 2, "image": self._image("tee.png"), "caption": "Style 2 T Shirt"},
                {"slot": 3, "image": self._image("pants.webp"), "caption": "Style 3 Sweatpant"},
            ],
        )

        self.assertEqual(ProductReferenceImage.objects.filter(lead=lead).count(), 3)

        opportunity = Opportunity.objects.create(
            lead=lead,
            stage="Prospecting",
            product_category="Hoodie",
            product_type="Streetwear",
        )
        link_reference_images_to_opportunity(lead, opportunity)
        self.assertEqual(len(reference_images_for_opportunity(opportunity)), 3)

        production = ProductionOrder.objects.create(
            title="Test Streetwear Co production",
            order_code="PO-REF-TEST",
            lead=lead,
            opportunity=opportunity,
            qty_total=300,
        )
        link_reference_images_to_production(opportunity=opportunity, production_order=production)
        self.assertEqual(len(reference_images_for_production(production)), 3)

    def test_opportunity_snapshot_appears_on_opportunity_detail(self):
        user = get_user_model().objects.create_superuser("snapshot-admin", "snapshot@example.com", "pass")
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("opportunity-detail.jpg"), "caption": "Opportunity Snapshot"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)

        self.client.force_login(user)
        response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opportunity Snapshot")
        self.assertContains(response, "opportunity-detail")

    def test_production_without_own_snapshot_uses_linked_opportunity_snapshot(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("opportunity-source.jpg"), "caption": "Opportunity Source"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        production = self._production(lead, opportunity)

        reference = reference_images_for_production(production)[0]
        snapshot = product_snapshot_for_production(production, reference)

        self.assertEqual(reference.caption, "Opportunity Source")
        self.assertIn("opportunity-source", snapshot["image_file"].name)
        self.assertEqual(snapshot["source_note"], "Using Opportunity snapshot")

    def test_production_own_snapshot_overrides_opportunity_snapshot(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("opportunity-fallback.jpg"), "caption": "Opportunity Fallback"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        production = self._production(
            lead,
            opportunity,
            style_image=self._image("production-specific.jpg"),
        )

        reference = reference_images_for_production(production)[0]
        snapshot = product_snapshot_for_production(production, reference)

        self.assertIn("production-specific", snapshot["image_file"].name)
        self.assertEqual(snapshot["source_note"], "Production specific snapshot")
        self.assertEqual(ProductReferenceImage.objects.get(lead=lead, slot=1).caption, "Opportunity Fallback")

    def test_production_falls_back_to_lead_snapshot_when_opportunity_has_none(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("lead-source.jpg"), "caption": "Lead Source"}],
        )
        opportunity = self._opportunity(lead)
        production = self._production(lead, opportunity)

        reference = reference_images_for_production(production)[0]
        snapshot = product_snapshot_for_production(production, reference)

        self.assertEqual(reference.caption, "Lead Source")
        self.assertIn("lead-source", snapshot["image_file"].name)
        self.assertEqual(snapshot["source_note"], "Using Lead snapshot")

    def test_existing_production_record_can_show_opportunity_snapshot_without_migration(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("existing-opportunity.jpg"), "caption": "Existing Opportunity"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        production = self._production(lead, opportunity)

        self.assertFalse(production.style_image)
        self.assertFalse(ProductReferenceImage.objects.filter(production_order=production).exists())

        reference = reference_images_for_production(production)[0]
        snapshot = product_snapshot_for_production(production, reference)

        self.assertIn("existing-opportunity", snapshot["image_file"].name)
        self.assertEqual(snapshot["source_note"], "Using Opportunity snapshot")

    def test_updating_opportunity_snapshot_updates_production_only_without_own_snapshot(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("before-update.jpg"), "caption": "Before Update"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        production = self._production(lead, opportunity)
        production_override = self._production(
            lead,
            opportunity,
            order_code="PO-SNAPSHOT-OVERRIDE",
            style_image=self._image("production-override.jpg"),
        )

        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("after-update.jpg"), "caption": "After Update"}],
        )

        fallback_reference = reference_images_for_production(production)[0]
        fallback_snapshot = product_snapshot_for_production(production, fallback_reference)
        override_reference = reference_images_for_production(production_override)[0]
        override_snapshot = product_snapshot_for_production(production_override, override_reference)

        self.assertIn("after-update", fallback_snapshot["image_file"].name)
        self.assertEqual(fallback_snapshot["source_note"], "Using Opportunity snapshot")
        self.assertIn("production-override", override_snapshot["image_file"].name)
        self.assertEqual(override_snapshot["source_note"], "Production specific snapshot")

    def test_uploading_production_snapshot_does_not_change_opportunity_snapshot(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("opportunity-original.jpg"), "caption": "Opportunity Original"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        production = self._production(lead, opportunity)

        production.style_image = self._image("manual-production.jpg")
        production.save(update_fields=["style_image"])

        opportunity_reference = reference_images_for_opportunity(opportunity)[0]
        production_snapshot = product_snapshot_for_production(
            production,
            reference_images_for_production(production)[0],
        )

        self.assertEqual(opportunity_reference.caption, "Opportunity Original")
        self.assertIn("opportunity-original", opportunity_reference.image.name)
        self.assertIn("manual-production", production_snapshot["image_file"].name)

    def test_moving_opportunity_to_production_keeps_snapshot_visible_without_duplicate_file(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("move-to-production.jpg"), "caption": "Move Snapshot"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        before_count = ProductReferenceImage.objects.count()
        before_image_name = ProductReferenceImage.objects.get(lead=lead, slot=1).image.name

        production = self._production(lead, opportunity)
        link_reference_images_to_production(opportunity=opportunity, production_order=production)

        self.assertEqual(ProductReferenceImage.objects.count(), before_count)
        reference = reference_images_for_production(production)[0]
        snapshot = product_snapshot_for_production(production, reference)
        self.assertEqual(reference.image.name, before_image_name)
        self.assertIn("move-to-production", snapshot["image_file"].name)
        self.assertEqual(snapshot["source_note"], "Using Opportunity snapshot")

    def test_production_detail_labels_opportunity_snapshot_fallback(self):
        user = get_user_model().objects.create_superuser("production-snapshot-admin", "prod@example.com", "pass")
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [{"slot": 1, "image": self._image("production-detail-fallback.jpg"), "caption": "Production Detail Fallback"}],
        )
        opportunity = self._opportunity(lead)
        link_reference_images_to_opportunity(lead, opportunity)
        production = self._production(lead, opportunity)

        self.client.force_login(user)
        response = self.client.get(reverse("production_detail", args=[production.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production Snapshot")
        self.assertContains(response, "Using Opportunity snapshot")
        self.assertContains(response, "production-detail-fallback")

    def test_more_than_three_reference_image_slots_are_blocked(self):
        lead = Lead.objects.create(account_brand="Slot Test", lead_type="outbound")
        with self.assertRaises(ValidationError):
            ProductReferenceImage.objects.create(
                lead=lead,
                slot=4,
                image=self._image("extra.jpg"),
                caption="Extra",
            )
