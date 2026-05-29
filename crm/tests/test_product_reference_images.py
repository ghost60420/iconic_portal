import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from crm.models import Lead, Opportunity, ProductReferenceImage, ProductionOrder
from crm.services.product_reference_images import (
    link_reference_images_to_opportunity,
    link_reference_images_to_production,
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

    def test_more_than_three_reference_image_slots_are_blocked(self):
        lead = Lead.objects.create(account_brand="Slot Test", lead_type="outbound")
        with self.assertRaises(ValidationError):
            ProductReferenceImage.objects.create(
                lead=lead,
                slot=4,
                image=self._image("extra.jpg"),
                caption="Extra",
            )
