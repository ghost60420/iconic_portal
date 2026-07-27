import io
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from crm.forms import ProductForm
from crm.models import Accessory, Fabric, LivingCatalogAudit, Product, ThreadOption, Trim
from crm.models_access import UserAccess
from crm.services.living_catalog import MAX_CATALOG_IMAGE_UPLOAD_BYTES, validate_catalog_image_file


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="iconic-living-catalog-test-media-")


def image_upload(name="catalog.jpg", size=(2200, 1400), color=(40, 120, 140)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LivingCatalogTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("catalog-admin", "admin@example.com", "pass")
        self.client.force_login(self.admin)

    def _grant_library(self, user, *, internal=False):
        access, _ = UserAccess.objects.get_or_create(user=user)
        access.can_library = True
        access.can_view_internal_costing = internal
        access.save()
        profile = getattr(user, "employee_profile", None)
        if profile:
            profile.display_name = user.username
            profile.department = "sales" if not internal else "accounts"
            profile.position = "sales_executive" if not internal else "accounts_manager"
            profile.save()

    def _fabric(self):
        return Fabric.objects.create(
            name="Heavy French Terry",
            composition="80% Cotton, 20% Polyester",
            gsm="400",
            best_use="Hoodies",
            status="Available",
        )

    def _materials(self):
        accessory = Accessory.objects.create(name="Matte Zipper", accessory_type="Zipper", color="Black", status="Available")
        trim = Trim.objects.create(name="Cuff Rib", trim_type="Cuff Rib", color="Black", status="Available")
        thread = ThreadOption.objects.create(name="Core Sewing Thread", thread_type="Sewing Thread", color="Black", status="Available")
        return accessory, trim, thread

    def _legacy_catalog_records(self):
        fabric = self._fabric()
        fabric.image.save("legacy-fabric.jpg", image_upload("legacy-fabric.jpg"), save=True)
        accessory, trim, thread = self._materials()
        accessory.image.save("legacy-accessory.jpg", image_upload("legacy-accessory.jpg"), save=True)
        trim.image.save("legacy-trim.jpg", image_upload("legacy-trim.jpg"), save=True)
        thread.image.save("legacy-thread.jpg", image_upload("legacy-thread.jpg"), save=True)
        product = Product.objects.create(
            name="Legacy Product",
            product_type="Streetwear",
            product_category="Hoodie",
            default_fabric="Legacy cotton",
            default_gsm="360",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Existing product record.",
            status="Available",
        )
        product.image.save("legacy-product.jpg", image_upload("legacy-product.jpg"), save=True)
        return {
            "products": product,
            "fabrics": fabric,
            "accessories": accessory,
            "trims": trim,
            "threads": thread,
        }

    def _product_post_data(self, fabric, accessory=None, trim=None, thread=None):
        return {
            "name": "Hoodie",
            "product_type": "Streetwear",
            "product_category": "Hoodie",
            "main_fabric": str(fabric.pk),
            "main_decoration": "Embroidery",
            "short_description": "Heavy fleece hoodie for premium streetwear.",
            "status": "Available",
            "fit": "Relaxed",
            "main_colour": "Black",
            "available_colours": "Black, Grey",
            "size_range": "XS-XXL",
            "default_moq": "100",
            "notes": "Public note",
            "tags": "hoodie,streetwear",
            "accessories": [str(accessory.pk)] if accessory else [],
            "trims": [str(trim.pk)] if trim else [],
            "threads": [str(thread.pk)] if thread else [],
            "internal_cost": "123.45",
            "suggested_selling_price": "234.56",
            "internal_notes": "Private margin note",
        }

    def test_product_create_three_images_related_material_and_resize(self):
        fabric = self._fabric()
        accessory, trim, thread = self._materials()
        response = self.client.post(
            reverse("product_add"),
            data={
                **self._product_post_data(fabric, accessory, trim, thread),
                "image": image_upload("one.jpg"),
                "image_2": image_upload("two.jpg"),
                "image_3": image_upload("three.jpg"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(name="Hoodie")
        self.assertEqual(product.main_fabric, fabric)
        self.assertEqual(product.accessories.count(), 1)
        self.assertEqual(product.trims.count(), 1)
        self.assertEqual(product.threads.count(), 1)
        self.assertTrue(product.image)
        self.assertTrue(product.image_2)
        self.assertTrue(product.image_3)

        with Image.open(product.image.path) as saved:
            self.assertLessEqual(max(saved.size), 1800)

        self.assertContains(response, "80% Cotton, 20% Polyester")
        self.assertContains(response, "400")
        self.assertEqual(
            LivingCatalogAudit.objects.filter(
                item_type="product",
                object_id=product.pk,
                action=LivingCatalogAudit.ACTION_IMAGE_ADDED,
            ).count(),
            3,
        )

    def test_current_library_urls_open_and_existing_images_render_once(self):
        records = self._legacy_catalog_records()

        home = self.client.get(reverse("library_home"))
        self.assertEqual(home.status_code, 200)
        self.assertEqual(home.content.decode().count("Open Catalog"), 5)

        url_names = {
            "products": ("products_list", "product_detail", "product_edit"),
            "fabrics": ("fabrics_list", "fabric_detail", "fabric_edit"),
            "accessories": ("accessories_list", "accessory_detail", "accessory_edit"),
            "trims": ("trims_list", "trim_detail", "trim_edit"),
            "threads": ("threads_list", "thread_detail", "thread_edit"),
        }
        for section, (list_name, detail_name, edit_name) in url_names.items():
            obj = records[section]
            list_response = self.client.get(reverse(list_name))
            self.assertEqual(list_response.status_code, 200)
            self.assertContains(list_response, obj.name)
            self.assertEqual(len(list_response.context["rows"]), 1)
            self.assertEqual(list_response.context["rows"][0]["object"].pk, obj.pk)

            detail_response = self.client.get(reverse(detail_name, args=[obj.pk]))
            self.assertEqual(detail_response.status_code, 200)
            self.assertContains(detail_response, obj.name)
            self.assertContains(detail_response, obj.image.url)

            edit_response = self.client.get(reverse(edit_name, args=[obj.pk]))
            self.assertEqual(edit_response.status_code, 200)
            self.assertContains(edit_response, obj.name)

    def test_required_fields_match_approved_living_catalog_scope(self):
        form = ProductForm(can_view_internal=False)

        for field_name in (
            "name",
            "product_type",
            "product_category",
            "main_fabric",
            "main_decoration",
            "short_description",
            "status",
        ):
            self.assertTrue(form.fields[field_name].required)

        for field_name in (
            "fit",
            "main_colour",
            "available_colours",
            "size_range",
            "default_moq",
            "notes",
            "tags",
        ):
            self.assertFalse(form.fields[field_name].required)

        self.assertNotIn("internal_cost", form.fields)
        self.assertNotIn("suggested_selling_price", form.fields)
        self.assertNotIn("internal_notes", form.fields)

    def test_image_replace_and_remove_records_audit_without_deleting_record(self):
        fabric = self._fabric()
        accessory, trim, thread = self._materials()
        self.client.post(
            reverse("product_add"),
            data={
                **self._product_post_data(fabric, accessory, trim, thread),
                "image": image_upload("one.jpg"),
                "image_2": image_upload("two.jpg"),
                "image_3": image_upload("three.jpg"),
            },
        )
        product = Product.objects.get(name="Hoodie")
        original_slot_2 = product.image_2.name

        response = self.client.post(
            reverse("product_edit", args=[product.pk]),
            data={
                **self._product_post_data(fabric, accessory, trim, thread),
                "image_2": image_upload("replacement.jpg", color=(90, 70, 150)),
                "remove_image_3": "1",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertNotEqual(product.image_2.name, original_slot_2)
        self.assertFalse(product.image_3)
        self.assertTrue(
            LivingCatalogAudit.objects.filter(
                item_type="product",
                object_id=product.pk,
                action=LivingCatalogAudit.ACTION_IMAGE_REPLACED,
            ).exists()
        )
        self.assertTrue(
            LivingCatalogAudit.objects.filter(
                item_type="product",
                object_id=product.pk,
                action=LivingCatalogAudit.ACTION_IMAGE_REMOVED,
            ).exists()
        )

    def test_sales_cannot_see_internal_pricing_in_detail_presentation_or_api(self):
        fabric = self._fabric()
        product = Product.objects.create(
            name="Private Price Hoodie",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Client safe product text.",
            status="Available",
            internal_cost="123.45",
            suggested_selling_price="234.56",
            internal_notes="Private margin note",
            default_price="345.67",
        )
        User = get_user_model()
        sales = User.objects.create_user("catalog-sales", "sales@example.com", "pass")
        self._grant_library(sales, internal=False)
        self.client.force_login(sales)

        detail = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Internal cost")
        self.assertNotContains(detail, "123.45")
        self.assertNotContains(detail, "Private margin note")

        presentation = self.client.get(reverse("product_presentation", args=[product.pk]))
        self.assertEqual(presentation.status_code, 200)
        self.assertNotContains(presentation, "123.45")
        self.assertNotContains(presentation, "Private margin note")

        api = self.client.get(reverse("library_item_safe_json", args=["products", product.pk]))
        payload = json.dumps(api.json())
        self.assertNotIn("internal_cost", payload)
        self.assertNotIn("123.45", payload)
        self.assertNotIn("Private margin note", payload)

    def test_presentation_mode_has_record_navigation_without_edit_or_internal_controls(self):
        fabric = self._fabric()
        first = Product.objects.create(
            name="Presentation First",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="First client-safe product.",
            status="Available",
            internal_cost="100.00",
            internal_notes="Private note",
        )
        second = Product.objects.create(
            name="Presentation Second",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="DTF",
            short_description="Second client-safe product.",
            status="Available",
        )

        first_response = self.client.get(reverse("product_presentation", args=[first.pk]))
        self.assertEqual(first_response.status_code, 200)
        self.assertContains(first_response, "Next")
        self.assertContains(first_response, reverse("product_presentation", args=[second.pk]))
        self.assertNotContains(first_response, "Edit")
        self.assertNotContains(first_response, "Internal Only")
        self.assertNotContains(first_response, "100.00")
        self.assertNotContains(first_response, "Private note")

        second_response = self.client.get(reverse("product_presentation", args=[second.pk]))
        self.assertEqual(second_response.status_code, 200)
        self.assertContains(second_response, "Previous")
        self.assertContains(second_response, reverse("product_presentation", args=[first.pk]))

    def test_sales_can_view_but_cannot_edit_archive_or_receive_internal_form_fields(self):
        records = self._legacy_catalog_records()
        product = records["products"]
        User = get_user_model()
        sales = User.objects.create_user("catalog-sales-no-edit", "sales-no-edit@example.com", "pass")
        self._grant_library(sales, internal=False)
        self.client.force_login(sales)

        view_response = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertEqual(view_response.status_code, 200)
        self.assertNotContains(view_response, "Internal Only")

        edit_response = self.client.get(reverse("product_edit", args=[product.pk]))
        self.assertEqual(edit_response.status_code, 403)

        archive_response = self.client.post(reverse("catalog_archive", args=["products", product.pk]))
        self.assertEqual(archive_response.status_code, 403)
        product.refresh_from_db()
        self.assertEqual(product.status, "Available")

        form_response = self.client.get(reverse("product_add"))
        self.assertEqual(form_response.status_code, 403)

    def test_user_without_library_permission_cannot_open_current_library_urls(self):
        User = get_user_model()
        user = User.objects.create_user("no-library", "no-library@example.com", "pass")
        UserAccess.objects.get_or_create(user=user)
        self.client.force_login(user)

        response = self.client.get(reverse("library_home"))

        self.assertEqual(response.status_code, 403)

    def test_responsive_layout_rules_render_for_home_list_detail_form_and_presentation(self):
        records = self._legacy_catalog_records()
        product = records["products"]

        home = self.client.get(reverse("library_home"))
        self.assertContains(home, "grid-template-columns:repeat(5")
        self.assertContains(home, "@media (max-width:1100px)")
        self.assertContains(home, "@media (max-width:760px)")

        list_response = self.client.get(reverse("products_list"))
        self.assertContains(list_response, "grid-template-columns:repeat(auto-fill")
        self.assertContains(list_response, "@media (max-width:980px)")
        self.assertContains(list_response, "@media (max-width:640px)")

        detail_response = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertContains(detail_response, "grid-template-columns:420px 1fr")
        self.assertContains(detail_response, "@media (max-width:900px)")

        form_response = self.client.get(reverse("product_edit", args=[product.pk]))
        self.assertContains(form_response, "grid-template-columns:minmax(0,1fr) 330px")
        self.assertContains(form_response, "@media (max-width:980px)")
        self.assertContains(form_response, "@media (max-width:620px)")

        presentation_response = self.client.get(reverse("product_presentation", args=[product.pk]))
        self.assertContains(presentation_response, "grid-template-columns:minmax(0,1.25fr) minmax(360px,.75fr)")
        self.assertContains(presentation_response, "@media (max-width:980px)")
        self.assertContains(presentation_response, "@media (max-width:620px)")

    def test_invalid_and_oversized_uploads_are_rejected(self):
        fabric = self._fabric()
        bad_type = image_upload("bad.gif")
        bad_type_form = ProductForm(
            data=self._product_post_data(fabric),
            files={"image": bad_type},
            can_view_internal=True,
        )
        self.assertFalse(bad_type_form.is_valid())
        self.assertIn("JPG", str(bad_type_form.errors))

        oversized = SimpleUploadedFile(
            "large.jpg",
            b"x" * (MAX_CATALOG_IMAGE_UPLOAD_BYTES + 1),
            content_type="image/jpeg",
        )
        with self.assertRaisesMessage(Exception, "8MB"):
            validate_catalog_image_file(oversized, 1)

    def test_internal_user_can_see_internal_section_on_detail(self):
        fabric = self._fabric()
        product = Product.objects.create(
            name="Accounts Visible Hoodie",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Client safe product text.",
            status="Available",
            internal_cost="123.45",
        )
        User = get_user_model()
        accounts = User.objects.create_user("catalog-accounts", "accounts@example.com", "pass")
        self._grant_library(accounts, internal=True)
        self.client.force_login(accounts)

        response = self.client.get(reverse("product_detail", args=[product.pk]))

        self.assertContains(response, "Internal Only")
        self.assertContains(response, "CAD 123.45")

    def test_create_material_sections_and_open_detail_pages(self):
        posts = [
            ("fabric_add", "fabric_detail", Fabric, {"name": "Rib Knit", "composition": "95% Cotton, 5% Spandex", "gsm": "260", "best_use": "Ribs", "status": "Available"}),
            ("accessory_add", "accessory_detail", Accessory, {"name": "Logo Patch", "accessory_type": "Patch", "color": "Black", "status": "Available"}),
            ("trim_add", "trim_detail", Trim, {"name": "Neck Rib", "trim_type": "Neck Rib", "color": "Black", "status": "Available"}),
            ("thread_add", "thread_detail", ThreadOption, {"name": "Embroidery Thread", "thread_type": "Embroidery Thread", "color": "Black", "status": "Available"}),
        ]

        for add_name, detail_name, model, data in posts:
            response = self.client.post(reverse(add_name), data={**data, "image": image_upload(f"{add_name}.jpg")}, follow=True)
            self.assertEqual(response.status_code, 200)
            obj = model.objects.get(name=data["name"])
            detail = self.client.get(reverse(detail_name, args=[obj.pk]))
            self.assertContains(detail, data["name"])

    def test_search_table_view_archive_and_restore(self):
        fabric = self._fabric()
        product = Product.objects.create(
            name="Searchable Hoodie",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="DTF",
            short_description="A product for search.",
            status="Available",
            tags="winter-catalog",
            main_colour="Navy",
        )

        list_response = self.client.get(reverse("products_list"), {"q": "winter-catalog", "view": "table"})
        self.assertContains(list_response, "Searchable Hoodie")
        self.assertContains(list_response, "Table View")

        archive = self.client.post(reverse("catalog_archive", args=["products", product.pk]), follow=True)
        self.assertEqual(archive.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.status, "Archived")
        self.assertFalse(product.is_active)

        restore = self.client.post(reverse("catalog_restore", args=["products", product.pk]), follow=True)
        self.assertEqual(restore.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.status, "Available")
        self.assertTrue(product.is_active)
