import io
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from crm.forms import ProductForm
from crm.models import Accessory, CRMAuditLog, Fabric, LivingCatalogAudit, Product, ThreadOption, Trim
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

    def _library_permission_defaults(self, *, department="", position=""):
        department = (department or "").strip().lower()
        position = (position or "").strip().lower()
        defaults = {
            "view": False,
            "add": False,
            "edit": False,
            "upload": False,
            "remove": False,
            "archive": False,
            "presentation": False,
            "pricing": False,
        }
        if position == "ceo":
            return {key: True for key in defaults}
        if position == "director":
            return {key: True for key in defaults}
        if position in {"production_manager", "factory_manager", "approved_manager"}:
            defaults.update(view=True, add=True, edit=True, upload=True, remove=True, presentation=True)
        elif department == "sales" or position in {"sales_manager", "sales_executive"}:
            defaults.update(view=True, presentation=True)
        elif department == "accounts" or position in {"accounts_manager", "accountant"}:
            defaults.update(view=True, pricing=True)
        elif department == "marketing":
            defaults.update(view=True, presentation=True)
        return defaults

    def _grant_library(
        self,
        user,
        *,
        internal=False,
        edit=None,
        department=None,
        position=None,
        view=None,
        add=None,
        upload=None,
        remove=None,
        archive=None,
        presentation=None,
        pricing=None,
    ):
        department_value = department if department is not None else ("accounts" if internal else "sales")
        position_value = position if position is not None else ("accounts_manager" if internal else "sales_executive")
        defaults = self._library_permission_defaults(department=department_value, position=position_value)
        if edit is not None:
            defaults["edit"] = edit
            if edit:
                defaults.update(view=True, add=True, upload=True)
        if internal:
            defaults["pricing"] = True
        overrides = {
            "view": view,
            "add": add,
            "upload": upload,
            "remove": remove,
            "archive": archive,
            "presentation": presentation,
            "pricing": pricing,
        }
        for key, value in overrides.items():
            if value is not None:
                defaults[key] = value
        if any(defaults[key] for key in defaults if key != "view"):
            defaults["view"] = True

        access, _ = UserAccess.objects.get_or_create(user=user)
        access.can_library = defaults["view"]
        access.can_add_library = defaults["add"]
        access.can_edit_library = defaults["edit"]
        access.can_upload_library_images = defaults["upload"]
        access.can_remove_library_images = defaults["remove"]
        access.can_archive_library = defaults["archive"]
        access.can_use_library_presentation = defaults["presentation"]
        access.can_view_library_pricing = defaults["pricing"]
        access.can_view_internal_costing = internal
        access.save()
        profile = getattr(user, "employee_profile", None)
        if profile:
            profile.display_name = user.username
            profile.department = department_value
            profile.position = position_value
            profile.save()

    def _library_user(self, username, *, internal=False, edit=None, department="sales", position="sales_executive", **permissions):
        User = get_user_model()
        user = User.objects.create_user(username, f"{username}@example.com", "pass")
        self._grant_library(user, internal=internal, edit=edit, department=department, position=position, **permissions)
        return user

    def _fabric(self):
        return Fabric.objects.create(
            name="Heavy French Terry",
            fabric_type="French Terry",
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

    def test_product_main_fabric_dropdown_has_useful_labels_without_dash_option(self):
        Fabric.objects.create(
            name="Mesh",
            fabric_type="Mesh",
            composition="100% Polyester",
            gsm="180",
            best_use="Sportswear",
            status="Available",
        )

        form = ProductForm(can_view_internal=False)
        choices = [(str(value), label) for value, label in form.fields["main_fabric"].choices]
        labels = [label for _value, label in choices]

        self.assertEqual(labels[0], "Select a fabric")
        self.assertIn("Mesh | 100% Polyester | 180 GSM", labels)
        self.assertNotIn("---------", labels)

    def test_product_add_form_renders_single_main_fabric_section_with_details(self):
        fabric = self._fabric()

        response = self.client.get(reverse("product_add"))
        html = response.content.decode()
        main_fabric_name = html.find('name="main_fabric"')
        main_fabric_select_start = html.rfind("<select", 0, main_fabric_name)
        main_fabric_select_end = html.find("</select>", main_fabric_name) + len("</select>")
        main_fabric_select = html[main_fabric_select_start:main_fabric_select_end]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('name="main_fabric"'), 1)
        self.assertContains(response, "Main Fabric")
        self.assertContains(response, "Select a fabric")
        self.assertContains(response, "Fabric Type")
        self.assertContains(response, "GSM")
        self.assertContains(response, "Composition")
        self.assertContains(response, fabric.fabric_type)
        self.assertContains(response, fabric.gsm)
        self.assertContains(response, fabric.composition)
        self.assertNotIn("---------", main_fabric_select)
        self.assertNotContains(response, '"main_fabric","accessories"')

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
        self.assertContains(response, "French Terry")
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

    def test_showroom_card_view_renders_useful_details_without_private_pricing(self):
        fabric = Fabric.objects.create(
            name="Sports Mesh",
            fabric_type="Mesh",
            composition="100% Polyester",
            gsm="180",
            best_use="Performance shorts",
            status="Available",
        )
        Product.objects.create(
            name="Performance Shorts",
            product_type="Activewear",
            product_category="Shorts",
            main_fabric=fabric,
            main_decoration="Sublimation",
            short_description="Lightweight training shorts.",
            status="Available",
            internal_cost="12.50",
            internal_notes="Private costing",
        )

        response = self.client.get(reverse("products_list"))

        self.assertContains(response, "lc-showroom-card")
        self.assertContains(response, "Performance Shorts")
        self.assertContains(response, "Activewear")
        self.assertContains(response, "Mesh | 180 GSM")
        self.assertContains(response, "Sublimation")
        self.assertContains(response, "Available")
        self.assertNotContains(response, "12.50")
        self.assertNotContains(response, "Private costing")

    def test_gallery_lightbox_controls_render_on_detail_and_presentation(self):
        fabric = self._fabric()
        product = Product.objects.create(
            name="Gallery Product",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Gallery-ready product.",
            status="Available",
        )
        product.image.save("gallery-one.jpg", image_upload("gallery-one.jpg"), save=True)
        product.image_2.save("gallery-two.jpg", image_upload("gallery-two.jpg"), save=True)
        product.image_3.save("gallery-three.jpg", image_upload("gallery-three.jpg"), save=True)

        detail = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertContains(detail, "data-catalog-gallery")
        self.assertContains(detail, "data-gallery-lightbox")
        self.assertContains(detail, "data-gallery-prev")
        self.assertContains(detail, "data-gallery-next")
        self.assertContains(detail, "Enlarge")
        self.assertContains(detail, product.image_2.url)
        self.assertContains(detail, product.image_3.url)

        presentation = self.client.get(reverse("product_presentation", args=[product.pk]))
        self.assertContains(presentation, "Exit Presentation")
        self.assertContains(presentation, "Full Screen")
        self.assertContains(presentation, "data-gallery-lightbox")
        self.assertContains(presentation, "data-gallery-prev")
        self.assertContains(presentation, "data-gallery-next")
        self.assertNotContains(presentation, "crm-nav-item")

    def test_search_and_filters_use_fabric_specs_gsm_and_decoration(self):
        fabric = Fabric.objects.create(
            name="Sports Mesh",
            fabric_type="Mesh",
            composition="100% Polyester",
            gsm="180",
            best_use="Activewear",
            status="Available",
        )
        product = Product.objects.create(
            name="Searchable Performance Shorts",
            product_type="Activewear",
            product_category="Shorts",
            main_fabric=fabric,
            main_decoration="Sublimation",
            short_description="Searchable by linked fabric specs.",
            status="Available",
            main_colour="Navy",
            tags="summer",
        )

        checks = [
            (reverse("products_list"), {"q": "100% Polyester"}),
            (reverse("products_list"), {"q": "180"}),
            (reverse("products_list"), {"q": "Sublimation"}),
            (reverse("products_list"), {"fabric": "Mesh"}),
            (reverse("products_list"), {"gsm": "180"}),
            (reverse("products_list"), {"decoration": "Sublimation"}),
        ]
        for url, params in checks:
            with self.subTest(params=params):
                response = self.client.get(url, params)
                self.assertContains(response, product.name)

        fabric_by_composition = self.client.get(reverse("fabrics_list"), {"composition": "Polyester"})
        self.assertContains(fabric_by_composition, fabric.name)
        fabric_by_gsm = self.client.get(reverse("fabrics_list"), {"gsm": "180"})
        self.assertContains(fabric_by_gsm, fabric.name)

    def test_related_materials_and_reverse_related_products_render(self):
        fabric = self._fabric()
        accessory, trim, thread = self._materials()
        product = Product.objects.create(
            name="Related Product",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Related material product.",
            status="Available",
        )
        product.accessories.add(accessory)
        product.trims.add(trim)
        product.threads.add(thread)

        product_detail = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertContains(product_detail, "Related Materials")
        self.assertContains(product_detail, fabric.name)
        self.assertContains(product_detail, accessory.name)
        self.assertContains(product_detail, trim.name)
        self.assertContains(product_detail, thread.name)

        fabric_detail = self.client.get(reverse("fabric_detail", args=[fabric.pk]))
        self.assertContains(fabric_detail, "Products using this fabric")
        self.assertContains(fabric_detail, product.name)

        for obj, url_name in ((accessory, "accessory_detail"), (trim, "trim_detail"), (thread, "thread_detail")):
            with self.subTest(section=url_name):
                response = self.client.get(reverse(url_name, args=[obj.pk]))
                self.assertContains(response, "Related products")
                self.assertContains(response, product.name)

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
            if field_name == "main_fabric":
                self.assertFalse(form.fields[field_name].required)
            else:
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

    def test_product_saves_with_temporary_fabric_details(self):
        response = self.client.post(
            reverse("product_add"),
            data={
                "name": "Mesh Training Tee",
                "product_type": "Activewear",
                "product_category": "T Shirt",
                "main_decoration": "Screen Print",
                "short_description": "Training tee with temporary fabric details.",
                "status": "Available",
                "use_temporary_fabric": "1",
                "new_fabric_name": "Performance Mesh",
                "new_fabric_type": "Mesh",
                "new_fabric_gsm": "180",
                "new_fabric_composition": "100% Polyester",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(name="Mesh Training Tee")
        self.assertIsNotNone(product.main_fabric)
        self.assertEqual(product.main_fabric.name, "Performance Mesh")
        self.assertEqual(product.main_fabric.fabric_type, "Mesh")
        self.assertEqual(product.main_fabric.gsm, "180")
        self.assertEqual(product.main_fabric.composition, "100% Polyester")
        self.assertContains(response, "Mesh")
        self.assertContains(response, "180")
        self.assertContains(response, "100% Polyester")

    def test_product_requires_fabric_selection_or_complete_temporary_details(self):
        response = self.client.post(
            reverse("product_add"),
            data={
                "name": "Missing Fabric Hoodie",
                "product_type": "Streetwear",
                "product_category": "Hoodie",
                "main_decoration": "Embroidery",
                "short_description": "Missing fabric should not save.",
                "status": "Available",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Missing Fabric Hoodie").exists())
        self.assertContains(response, "Select a fabric or use Add new fabric.")

        incomplete = self.client.post(
            reverse("product_add"),
            data={
                "name": "Incomplete Temp Fabric Hoodie",
                "product_type": "Streetwear",
                "product_category": "Hoodie",
                "main_decoration": "Embroidery",
                "short_description": "Incomplete fabric should not save.",
                "status": "Available",
                "use_temporary_fabric": "1",
                "new_fabric_name": "Partial Fabric",
                "new_fabric_gsm": "200",
            },
        )
        self.assertContains(incomplete, "Select the fabric type.")
        self.assertContains(incomplete, "Enter the fabric composition.")

    def test_existing_product_fabric_text_still_displays_and_remains_editable(self):
        product = Product.objects.create(
            name="Legacy Text Fabric Product",
            product_type="Streetwear",
            product_category="Hoodie",
            default_fabric="Legacy cotton",
            default_gsm="360",
            main_decoration="Embroidery",
            short_description="Existing text fabric product.",
            status="Available",
        )

        detail = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertContains(detail, "Legacy cotton")
        self.assertContains(detail, "360")

        edit = self.client.post(
            reverse("product_edit", args=[product.pk]),
            data={
                "name": "Legacy Text Fabric Product",
                "product_type": "Streetwear",
                "product_category": "Hoodie",
                "main_decoration": "Embroidery",
                "short_description": "Updated text fabric product.",
                "status": "Available",
            },
            follow=True,
        )

        self.assertEqual(edit.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.default_fabric, "Legacy cotton")
        self.assertEqual(product.default_gsm, "360")
        self.assertEqual(product.short_description, "Updated text fabric product.")

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
        self.assertNotContains(first_response, "Audit")
        self.assertNotContains(first_response, self.admin.username)
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
        self.assertNotContains(view_response, reverse("product_edit", args=[product.pk]))
        self.assertNotContains(view_response, "Archive this record?")

        edit_response = self.client.get(reverse("product_edit", args=[product.pk]))
        self.assertEqual(edit_response.status_code, 403)

        archive_response = self.client.post(reverse("catalog_archive", args=["products", product.pk]))
        self.assertEqual(archive_response.status_code, 403)
        product.refresh_from_db()
        self.assertEqual(product.status, "Available")

        form_response = self.client.get(reverse("product_add"))
        self.assertEqual(form_response.status_code, 403)

    def test_role_default_library_permissions_are_enforced(self):
        fabric = self._fabric()
        product = Product.objects.create(
            name="Role Default Product",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Role default product.",
            status="Available",
            internal_cost="123.45",
        )
        hidden_product = Product.objects.create(
            name="Role Default Development Product",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Marketing should not see this.",
            status="Development",
        )

        director = self._library_user("catalog-director-default", department="management", position="director")
        self.client.force_login(director)
        archive = self.client.post(reverse("catalog_archive", args=["products", product.pk]), follow=True)
        self.assertEqual(archive.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.status, "Archived")
        restore = self.client.post(reverse("catalog_restore", args=["products", product.pk]), follow=True)
        self.assertEqual(restore.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.status, "Available")

        sales = self._library_user("catalog-sales-default", department="sales", position="sales_executive")
        self.client.force_login(sales)
        self.assertEqual(self.client.get(reverse("product_detail", args=[product.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("product_presentation", args=[product.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("product_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("product_edit", args=[product.pk])).status_code, 403)

        accounts = self._library_user("catalog-accounts-default", internal=True, department="accounts", position="accountant")
        self.client.force_login(accounts)
        accounts_detail = self.client.get(reverse("product_detail", args=[product.pk]))
        self.assertContains(accounts_detail, "Internal Only")
        self.assertContains(accounts_detail, "CAD 123.45")
        self.assertEqual(
            self.client.post(
                reverse("product_add"),
                data={**self._product_post_data(fabric), "name": "Accounts Upload Blocked", "image": image_upload("accounts.jpg")},
            ).status_code,
            403,
        )

        marketing = self._library_user("catalog-marketing-default", department="marketing", position="staff")
        self.client.force_login(marketing)
        marketing_list = self.client.get(reverse("products_list"))
        self.assertContains(marketing_list, product.name)
        self.assertNotContains(marketing_list, hidden_product.name)
        self.assertEqual(self.client.get(reverse("product_presentation", args=[product.pk])).status_code, 200)

    def test_library_image_upload_allowed_for_approved_roles_and_edit_permission(self):
        fabric = self._fabric()
        User = get_user_model()
        sales_group, _ = Group.objects.get_or_create(name="Sales")
        flagged_sales = self._library_user("catalog-flagged-sales", edit=True, department="sales", position="sales_executive")
        flagged_sales.groups.add(sales_group)
        allowed_users = [
            self._library_user("catalog-ceo", department="management", position="ceo"),
            User.objects.create_superuser("catalog-super-admin", "catalog-super-admin@example.com", "pass"),
            self._library_user("catalog-director", department="management", position="director"),
            self._library_user("catalog-production-manager", department="production", position="production_manager"),
            self._library_user("catalog-factory-manager", department="production", position="factory_manager"),
            self._library_user("catalog-approved-manager", department="management", position="approved_manager"),
            self._library_user("catalog-flagged-accounts", internal=True, edit=True, department="accounts", position="accountant"),
            flagged_sales,
        ]

        for user in allowed_users:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                product_name = f"{user.username} image product"
                response = self.client.post(
                    reverse("product_add"),
                    data={
                        **self._product_post_data(fabric),
                        "name": product_name,
                        "image": image_upload(f"{user.username}.jpg"),
                    },
                    follow=True,
                )

                self.assertEqual(response.status_code, 200)
                product = Product.objects.get(name=product_name)
                self.assertTrue(product.image)

    def test_library_editor_can_upload_three_replace_remove_and_save_record(self):
        fabric = self._fabric()
        manager = self._library_user(
            "catalog-image-manager",
            department="production",
            position="production_manager",
        )
        self.client.force_login(manager)

        create_response = self.client.post(
            reverse("product_add"),
            data={
                **self._product_post_data(fabric),
                "name": "Manager Image Product",
                "image": image_upload("manager-one.jpg"),
                "image_2": image_upload("manager-two.jpg"),
                "image_3": image_upload("manager-three.jpg"),
            },
            follow=True,
        )

        self.assertEqual(create_response.status_code, 200)
        product = Product.objects.get(name="Manager Image Product")
        self.assertTrue(product.image)
        self.assertTrue(product.image_2)
        self.assertTrue(product.image_3)
        original_slot_2 = product.image_2.name

        edit_response = self.client.post(
            reverse("product_edit", args=[product.pk]),
            data={
                **self._product_post_data(fabric),
                "name": "Manager Image Product",
                "short_description": "Updated by an approved Library editor.",
                "image_2": image_upload("manager-replacement.jpg", color=(90, 70, 150)),
                "remove_image_3": "1",
            },
            follow=True,
        )

        self.assertEqual(edit_response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.short_description, "Updated by an approved Library editor.")
        self.assertNotEqual(product.image_2.name, original_slot_2)
        self.assertFalse(product.image_3)

    def test_image_permissions_are_checked_separately_from_item_edit(self):
        fabric = self._fabric()
        editor = self._library_user(
            "catalog-editor-no-image",
            department="production",
            position="staff",
            view=True,
            add=True,
            edit=True,
            upload=False,
            remove=False,
            presentation=True,
        )
        self.client.force_login(editor)

        create_response = self.client.post(
            reverse("product_add"),
            data={**self._product_post_data(fabric), "name": "Text Only Product"},
            follow=True,
        )
        self.assertEqual(create_response.status_code, 200)
        product = Product.objects.get(name="Text Only Product")

        upload_response = self.client.post(
            reverse("product_edit", args=[product.pk]),
            data={**self._product_post_data(fabric), "name": product.name, "image": image_upload("blocked.jpg")},
        )
        self.assertEqual(upload_response.status_code, 403)
        product.refresh_from_db()
        self.assertFalse(product.image)

        product.image.save("existing.jpg", image_upload("existing.jpg"), save=True)
        remove_response = self.client.post(
            reverse("product_edit", args=[product.pk]),
            data={**self._product_post_data(fabric), "name": product.name, "remove_image": "1"},
        )
        self.assertEqual(remove_response.status_code, 403)
        product.refresh_from_db()
        self.assertTrue(product.image)

    def test_current_can_edit_library_users_keep_mapped_add_edit_upload_access(self):
        fabric = self._fabric()
        legacy_editor = self._library_user(
            "catalog-legacy-editor",
            department="production",
            position="staff",
            view=True,
            add=True,
            edit=True,
            upload=True,
            remove=False,
            archive=False,
            presentation=True,
        )
        self.client.force_login(legacy_editor)

        response = self.client.post(
            reverse("product_add"),
            data={**self._product_post_data(fabric), "name": "Legacy Editor Upload", "image": image_upload("legacy-editor.jpg")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(name="Legacy Editor Upload")
        self.assertTrue(product.image)
        remove_response = self.client.post(
            reverse("product_edit", args=[product.pk]),
            data={**self._product_post_data(fabric), "name": product.name, "remove_image": "1"},
        )
        self.assertEqual(remove_response.status_code, 403)

    def test_library_view_only_users_cannot_upload_by_form_or_direct_request(self):
        records = self._legacy_catalog_records()
        product = records["products"]
        fabric = records["fabrics"]
        original_image = product.image.name
        sales_group, _ = Group.objects.get_or_create(name="Sales")
        accounts_group, _ = Group.objects.get_or_create(name="Accounts")
        marketing_group, _ = Group.objects.get_or_create(name="Marketing")
        blocked_sales = self._library_user("catalog-blocked-sales", department="sales", position="sales_executive")
        blocked_sales.groups.add(sales_group)
        blocked_accounts = self._library_user("catalog-blocked-accounts", internal=True, department="accounts", position="accountant")
        blocked_accounts.groups.add(accounts_group)
        blocked_marketing = self._library_user("catalog-blocked-marketing", department="marketing", position="staff")
        blocked_marketing.groups.add(marketing_group)
        blocked_users = [
            blocked_sales,
            blocked_accounts,
            blocked_marketing,
            self._library_user("catalog-blocked-viewer", department="production", position="staff", view=True),
        ]

        for user in blocked_users:
            with self.subTest(user=user.username):
                self.client.force_login(user)

                detail_response = self.client.get(reverse("product_detail", args=[product.pk]))
                self.assertEqual(detail_response.status_code, 200)
                self.assertContains(detail_response, product.image.url)

                add_form_response = self.client.get(reverse("product_add"))
                self.assertEqual(add_form_response.status_code, 403)

                add_post_response = self.client.post(
                    reverse("product_add"),
                    data={
                        **self._product_post_data(fabric),
                        "name": f"{user.username} blocked product",
                        "image": image_upload(f"{user.username}-blocked.jpg"),
                    },
                )
                self.assertEqual(add_post_response.status_code, 403)
                self.assertFalse(Product.objects.filter(name=f"{user.username} blocked product").exists())

                edit_response = self.client.post(
                    reverse("product_edit", args=[product.pk]),
                    data={
                        **self._product_post_data(fabric),
                        "name": product.name,
                        "image": image_upload(f"{user.username}-direct.jpg"),
                        "remove_image": "1",
                    },
                )
                self.assertEqual(edit_response.status_code, 403)
                product.refresh_from_db()
                self.assertEqual(product.image.name, original_image)

    def test_library_permission_section_is_available_on_existing_access_page(self):
        response = self.client.get(reverse("access_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Library Permissions")
        self.assertContains(response, "Controls access to Products, Fabrics, Accessories, Trims, Threads, images, Presentation Mode, and internal pricing.")
        for label in (
            "View Library",
            "Add Library Items",
            "Edit Library Items",
            "Upload Images",
            "Remove Images",
            "Archive and Restore",
            "Use Presentation Mode",
            "View Internal Pricing",
        ):
            self.assertContains(response, label)

    def test_ceo_tools_user_can_manage_library_edit_permission_without_superuser_access(self):
        ceo = self._library_user("catalog-ceo-access", department="management", position="ceo")
        ceo.access.can_view_ceo_tools = True
        ceo.access.save()
        target = self._library_user("catalog-permission-target", department="production", position="staff")
        self.client.force_login(ceo)

        response = self.client.get(reverse("access_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Library Permissions")
        self.assertContains(response, "Only superusers can grant or remove CEO tools access.")

        post_response = self.client.post(
            reverse("access_list"),
            data={
                "user_id": str(target.pk),
                f"user_{target.pk}-role": UserAccess.ROLE_BD,
                f"user_{target.pk}-can_library": "on",
                f"user_{target.pk}-can_add_library": "on",
                f"user_{target.pk}-can_edit_library": "on",
                f"user_{target.pk}-can_upload_library_images": "on",
            },
            follow=True,
        )
        self.assertEqual(post_response.status_code, 200)
        target.access.refresh_from_db()
        self.assertTrue(target.access.can_library)
        self.assertTrue(target.access.can_add_library)
        self.assertTrue(target.access.can_edit_library)
        self.assertTrue(target.access.can_upload_library_images)
        self.assertFalse(target.access.can_view_ceo_tools)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="library_permissions",
                record_id=str(target.pk),
                field_name="library_permissions",
            ).exists()
        )

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

    def test_presentation_mode_displays_fabric_type_gsm_and_composition(self):
        fabric = self._fabric()
        product = Product.objects.create(
            name="Presentation Fabric Product",
            product_type="Streetwear",
            product_category="Hoodie",
            main_fabric=fabric,
            main_decoration="Embroidery",
            short_description="Client safe product text.",
            status="Available",
        )

        response = self.client.get(reverse("product_presentation", args=[product.pk]))

        self.assertContains(response, "Fabric type")
        self.assertContains(response, "French Terry")
        self.assertContains(response, "GSM")
        self.assertContains(response, "400")
        self.assertContains(response, "Composition")
        self.assertContains(response, "80% Cotton, 20% Polyester")

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
