import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from crm.models import Customer, Lead, Opportunity, ProductReferenceImage, ProductionOrder
from crm.services.product_reference_images import (
    link_reference_images_to_opportunity,
    link_reference_images_to_production,
    product_snapshot_for_opportunity,
    product_snapshot_for_production,
    reference_images_for_opportunity,
    reference_images_for_production,
    save_reference_images_for_lead,
    save_reference_images_for_opportunity,
)


MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ProductReferenceImageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def _image(self, name):
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "jpg"
        image_format = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(ext, "JPEG")
        content_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image_format]
        buffer = io.BytesIO()
        Image.new("RGB", (12, 12), color=(38, 99, 235)).save(buffer, format=image_format)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)

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

    def test_six_reference_images_are_allowed_and_seventh_is_blocked(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [
                {"slot": slot, "image": self._image(f"style-{slot}.jpg"), "caption": f"Style {slot}"}
                for slot in range(1, 7)
            ],
        )

        self.assertEqual(ProductReferenceImage.objects.filter(lead=lead).count(), 6)
        self.assertEqual(len(reference_images_for_lead := list(lead.product_reference_images.order_by("slot"))), 6)
        self.assertEqual(reference_images_for_lead[-1].caption, "Style 6")

        with self.assertRaises(ValidationError):
            ProductReferenceImage.objects.create(
                lead=lead,
                slot=7,
                image=self._image("style-7.jpg"),
            )

    def test_replace_image_four_only_and_remove_image_five_only(self):
        lead = self._lead()
        save_reference_images_for_lead(
            lead,
            [
                {"slot": slot, "image": self._image(f"before-{slot}.jpg"), "caption": f"Before {slot}"}
                for slot in range(1, 7)
            ],
        )
        before = {image.slot: (image.pk, image.image.name, image.caption) for image in ProductReferenceImage.objects.filter(lead=lead)}

        save_reference_images_for_lead(
            lead,
            [{"slot": 4, "image": self._image("after-4.jpg"), "caption": "After 4"}],
        )
        after_replace = {image.slot: (image.pk, image.image.name, image.caption) for image in ProductReferenceImage.objects.filter(lead=lead)}

        self.assertEqual(after_replace[1], before[1])
        self.assertEqual(after_replace[2], before[2])
        self.assertEqual(after_replace[3], before[3])
        self.assertEqual(after_replace[5], before[5])
        self.assertEqual(after_replace[6], before[6])
        self.assertEqual(after_replace[4][0], before[4][0])
        self.assertIn("after-4", after_replace[4][1])
        self.assertEqual(after_replace[4][2], "After 4")

        save_reference_images_for_lead(
            lead,
            [{"slot": 5, "image": None, "caption": "", "remove": True}],
        )

        self.assertFalse(ProductReferenceImage.objects.filter(lead=lead, slot=5).exists())
        self.assertEqual(ProductReferenceImage.objects.filter(lead=lead).count(), 5)
        self.assertTrue(ProductReferenceImage.objects.filter(lead=lead, slot=4).exists())

    def test_invalid_type_and_large_file_are_blocked(self):
        lead = self._lead()
        with self.assertRaises(ValidationError):
            save_reference_images_for_lead(
                lead,
                [{"slot": 1, "image": SimpleUploadedFile("bad.gif", b"bad", content_type="image/gif"), "caption": ""}],
            )

        with self.assertRaises(ValidationError):
            save_reference_images_for_lead(
                lead,
                [
                    {
                        "slot": 1,
                        "image": SimpleUploadedFile(
                            "large.jpg",
                            b"0" * (ProductReferenceImage.MAX_UPLOAD_SIZE_BYTES + 1),
                            content_type="image/jpeg",
                        ),
                        "caption": "",
                    }
                ],
            )

        self.assertEqual(ProductReferenceImage.objects.filter(lead=lead).count(), 0)

    def test_opportunity_only_reference_images_flow_to_production_without_duplicates(self):
        customer = Customer.objects.create(account_brand="Direct Customer", contact_name="Buyer")
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=customer,
            stage="Prospecting",
            product_category="Hoodie",
            product_type="Streetwear",
            moq_units=300,
        )
        save_reference_images_for_opportunity(
            opportunity,
            [
                {"slot": slot, "image": self._image(f"direct-{slot}.jpg"), "caption": f"Direct {slot}"}
                for slot in range(1, 7)
            ],
        )
        before_ids = list(ProductReferenceImage.objects.filter(opportunity=opportunity).order_by("slot").values_list("id", flat=True))

        production = ProductionOrder.objects.create(
            title="Direct production",
            order_code="PO-DIRECT-REF",
            customer=customer,
            opportunity=opportunity,
            qty_total=300,
        )
        link_reference_images_to_production(opportunity=opportunity, production_order=production)
        after_ids = [image.id for image in reference_images_for_production(production)]

        self.assertEqual(after_ids, before_ids)
        self.assertEqual(ProductReferenceImage.objects.count(), 6)
        self.assertEqual(ProductReferenceImage.objects.filter(production_order=production).count(), 6)

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

    def test_opportunity_edit_shows_existing_six_image_controls(self):
        user = get_user_model().objects.create_superuser("opportunity-edit-admin", "opp-edit@example.com", "pass")
        customer = Customer.objects.create(account_brand="Edit Customer", contact_name="Buyer")
        opportunity = Opportunity.objects.create(
            customer=customer,
            stage="Proposal",
            product_category="Hoodie",
            product_type="Streetwear",
            moq_units=300,
        )
        save_reference_images_for_opportunity(
            opportunity,
            [
                {"slot": slot, "image": self._image(f"edit-existing-{slot}.jpg"), "caption": f"Edit Style {slot}"}
                for slot in range(1, 7)
            ],
        )

        self.client.force_login(user)
        response = self.client.get(reverse("opportunity_edit", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'enctype="multipart/form-data"')
        for slot in range(1, 7):
            self.assertContains(response, f'reference_image_{slot}')
            self.assertContains(response, f'reference_caption_{slot}')
            self.assertContains(response, f'reference_remove_{slot}')
            self.assertContains(response, f"Edit Style {slot}")
            self.assertContains(response, f"edit-existing-{slot}")

    def test_opportunity_edit_replaces_slot_four_and_removes_slot_five_only(self):
        user = get_user_model().objects.create_superuser("opportunity-edit-save-admin", "opp-edit-save@example.com", "pass")
        customer = Customer.objects.create(account_brand="Edit Save Customer", contact_name="Buyer")
        opportunity = Opportunity.objects.create(
            customer=customer,
            stage="Proposal",
            product_category="Hoodie",
            product_type="Streetwear",
            moq_units=300,
            order_currency="CAD",
            order_value_usd="7500.00",
            fx_rate_bdt_per_usd="85.0000",
            notes="Before image edit",
        )
        save_reference_images_for_opportunity(
            opportunity,
            [
                {"slot": slot, "image": self._image(f"before-edit-{slot}.jpg"), "caption": f"Before Edit {slot}"}
                for slot in range(1, 7)
            ],
        )
        before = {
            image.slot: (image.pk, image.image.name, image.caption)
            for image in ProductReferenceImage.objects.filter(opportunity=opportunity)
        }

        self.client.force_login(user)
        response = self.client.post(
            reverse("opportunity_edit", args=[opportunity.pk]),
            {
                "product_type": "Streetwear",
                "product_category": "Hoodie",
                "moq_units": "300",
                "order_currency": "CAD",
                "order_value_usd": "7500.00",
                "fx_rate_bdt_per_usd": "85.0000",
                "notes": "After image edit",
                "reference_caption_1": "Before Edit 1",
                "reference_caption_2": "Before Edit 2",
                "reference_caption_3": "Before Edit 3",
                "reference_caption_4": "After Edit 4",
                "reference_caption_5": "Before Edit 5",
                "reference_caption_6": "Before Edit 6",
                "reference_image_4": self._image("after-edit-4.jpg"),
                "reference_remove_5": "1",
            },
        )

        self.assertRedirects(response, reverse("opportunity_detail", args=[opportunity.pk]))
        after = {
            image.slot: (image.pk, image.image.name, image.caption)
            for image in ProductReferenceImage.objects.filter(opportunity=opportunity)
        }

        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2])
        self.assertEqual(after[3], before[3])
        self.assertEqual(after[6], before[6])
        self.assertNotIn(5, after)
        self.assertEqual(after[4][0], before[4][0])
        self.assertIn("after-edit-4", after[4][1])
        self.assertEqual(after[4][2], "After Edit 4")
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.notes, "After image edit")

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

    def test_more_than_six_reference_image_slots_are_blocked(self):
        lead = Lead.objects.create(account_brand="Slot Test", lead_type="outbound")
        ProductReferenceImage.objects.create(
            lead=lead,
            slot=6,
            image=self._image("slot-six.jpg"),
            caption="Slot six",
        )
        with self.assertRaises(ValidationError):
            ProductReferenceImage.objects.create(
                lead=lead,
                slot=7,
                image=self._image("extra.jpg"),
                caption="Extra",
            )
