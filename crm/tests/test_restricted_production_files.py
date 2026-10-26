"""Private media delivery on synthetic records and an isolated temporary MEDIA_ROOT."""
import base64
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import Lead, LeadComment, ProductReferenceImage, ProductionOrder, ProductionOrderAttachment, ProductionProgressPhoto
from crm.models_access import UserAccess
from crm.services.restricted_production import RESTRICTED_PRODUCTION_GROUP


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)


class RestrictedProductionFileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.user = users.objects.create_user("private-media-factory", pk=16)
        cls.user.groups.add(Group.objects.get_or_create(name=RESTRICTED_PRODUCTION_GROUP)[0])
        cls.employee = users.objects.create_user("private-media-employee")
        cls.employee.groups.add(Group.objects.get_or_create(name="Production")[0])
        cls.admin = users.objects.create_superuser("private-media-admin", password="test")
        cls.lead_employee = users.objects.create_user("private-media-leads-only")
        flags = {field.name: False for field in UserAccess._meta.fields if field.name.startswith("can_")}
        flags["can_leads"] = True
        UserAccess.objects.filter(user=cls.lead_employee).update(**flags)
        cls.order = ProductionOrder.objects.create(
            title="Assigned manufacturing order", assigned_production_manager=cls.user,
            style_image="production_styles/assigned.png",
        )
        cls.other = ProductionOrder.objects.create(
            title="Other manufacturing order", assigned_production_manager=cls.employee,
            style_image="production_styles/other.png",
        )

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.user)
        self.write(self.order.style_image.name)
        self.write(self.other.style_image.name)

    def write(self, path, content=PNG):
        target = self.media / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def url(self, path):
        return reverse("protected_media", kwargs={"path": path})

    def get_file(self, path, **extra):
        return self.client.get(self.url(path), **extra)

    def assert_private(self, response):
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertNotIn("ETag", response.headers)
        self.assertNotIn("Last-Modified", response.headers)

    def assert_download(self, path, content=PNG):
        response = self.get_file(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), content)
        self.assert_private(response)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response.headers["Content-Security-Policy"])
        return response

    def reference(self, path, order=None, **kwargs):
        self.write(path)
        return ProductReferenceImage.objects.create(
            production_order=order or self.order, image=path, **kwargs,
        )

    def test_anonymous_requests_cannot_read_production_media(self):
        self.client.logout()
        for path in (self.order.style_image.name, "production_progress/guess.png", "production_attachments/guess.pdf"):
            with self.subTest(path=path):
                response = self.get_file(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].startswith(reverse("login")))
                self.assert_private(response)

    def test_existing_public_images_remain_public_until_linked_to_production(self):
        self.client.logout()
        for path in ("catalog/material.png", "products/public.png", "product_reference_images/public.png"):
            with self.subTest(path=path):
                self.write(path)
                self.assert_download(path)
        path = "catalog/material.png"
        ProductionOrder.objects.filter(pk=self.order.pk).update(style_image=path)
        self.assertEqual(self.get_file(path).status_code, 302)
        self.client.force_login(self.user)
        self.assert_download(path)
        self.client.logout()
        ProductionOrder.objects.filter(pk=self.order.pk).update(style_image="")
        self.assert_download(path)

    def test_public_images_are_still_denied_to_restricted_account(self):
        path = "products/public.png"
        self.write(path)
        self.assertEqual(self.get_file(path).status_code, 404)
        self.client.logout()
        self.assert_download(path)

    def test_public_looking_reference_becomes_protected_by_its_order_link(self):
        reference = self.reference("products/shared-reference.png")
        self.client.logout()
        self.assertEqual(self.get_file(reference.image.name).status_code, 302)
        self.client.force_login(self.user)
        self.assert_download(reference.image.name)
        ProductReferenceImage.objects.filter(pk=reference.pk).update(production_order=self.other)
        self.assertEqual(self.get_file(reference.image.name).status_code, 404)

    def test_production_comment_attachments_cannot_use_the_public_media_fallback(self):
        path = "chatter_attachments/order-note.png"
        self.write(path)
        LeadComment.objects.create(production=self.other, content="Production attachment", attachment=path)
        self.client.logout()
        self.assertEqual(self.get_file(path).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.get_file(path).status_code, 404)
        self.client.force_login(self.employee)
        self.assert_download(path)
        self.client.force_login(self.admin)
        self.assert_download(path)
        # General, unlinked media keeps the existing public behavior.
        public = "chatter_attachments/general.png"
        self.write(public)
        LeadComment.objects.create(content="General attachment", attachment=public)
        self.client.logout()
        self.assert_download(public)

    def test_assigned_style_progress_and_explicit_reference_images_work(self):
        photo_path = "production_progress/assigned.png"
        self.write(photo_path)
        ProductionProgressPhoto.objects.create(order=self.order, stage="qc", image=photo_path)
        reference = self.reference("product_reference_images/assigned.png")
        for path in (self.order.style_image.name, photo_path, reference.image.name):
            with self.subTest(path=path):
                response = self.assert_download(path)
                self.assertEqual(response.headers["Content-Type"], "image/png")
                self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))

    def test_unassigned_images_and_assignment_revocation_are_denied(self):
        photo_path = "production_progress/unassigned.png"
        self.write(photo_path)
        ProductionProgressPhoto.objects.create(order=self.other, stage="qc", image=photo_path)
        reference = self.reference("product_reference_images/unassigned.png", self.other)
        for path in (self.other.style_image.name, photo_path, reference.image.name):
            response = self.get_file(path)
            self.assertEqual(response.status_code, 404)
            self.assert_private(response)
        self.assert_download(self.order.style_image.name)
        ProductionOrder.objects.filter(pk=self.order.pk).update(assigned_production_manager=None)
        response = self.get_file(self.order.style_image.name, HTTP_IF_MODIFIED_SINCE="Wed, 21 Oct 2030 07:28:00 GMT")
        self.assertEqual(response.status_code, 404)
        self.assert_private(response)

    def test_reference_requires_its_explicit_production_assignment(self):
        lead = Lead.objects.create(account_brand="Private shared lead")
        ProductionOrder.objects.filter(pk=self.order.pk).update(lead=lead)
        reference = self.reference("product_reference_images/shared.png", self.other, lead=lead)
        # A matching lead is not an assignment; re-linking the production image
        # revokes the previous assignee even if the old order still has that lead.
        self.assertEqual(self.get_file(reference.image.name).status_code, 404)
        ProductReferenceImage.objects.filter(pk=reference.pk).update(production_order=self.order)
        self.assert_download(reference.image.name)
        ProductReferenceImage.objects.filter(pk=reference.pk).update(production_order=self.other)
        self.assertEqual(self.get_file(reference.image.name).status_code, 404)

    def test_unrelated_files_and_arbitrary_order_attachments_are_denied(self):
        for path in ("customer_files/contact.png", "production_attachments/invoice.png", "production_attachments/spec.pdf"):
            self.write(path)
            if path.startswith("production_attachments/"):
                ProductionOrderAttachment.objects.create(order=self.order, file=path)
            self.assertEqual(self.get_file(path).status_code, 404)
        self.write("production_styles/guessed.png")
        self.assertEqual(self.get_file("production_styles/guessed.png").status_code, 404)

    def test_legacy_file_location_uses_database_assignment_not_directory_name(self):
        path = "legacy/renamed-style.png"
        self.write(path)
        ProductionOrder.objects.filter(pk=self.order.pk).update(style_image=path)
        self.assert_download(path)
        ProductionOrder.objects.filter(pk=self.order.pk).update(assigned_production_manager=self.employee)
        self.assertEqual(self.get_file(path).status_code, 404)
        self.client.force_login(self.employee)
        self.assert_download(path)

    def test_html_svg_and_other_non_photo_extensions_are_denied_to_restricted_user(self):
        for suffix in ("html", "svg", "pdf", "txt"):
            path = f"production_styles/active.{suffix}"
            self.write(path, b"<script>window.BAD=true</script>")
            ProductionOrder.objects.filter(pk=self.order.pk).update(style_image=path)
            self.assertEqual(self.get_file(path).status_code, 404)

    def test_html_disguised_as_image_cannot_be_rendered_as_active_html(self):
        payload = b"<html><script>window.BAD=true</script></html>"
        self.write(self.order.style_image.name, payload)
        response = self.assert_download(self.order.style_image.name, payload)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(response.headers["Content-Security-Policy"], "default-src 'none'; sandbox")

    def test_paths_cannot_escape_media_root_or_follow_symlinks(self):
        self.client.force_login(self.admin)
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.png"
            secret.write_bytes(b"outside secret")
            (self.media / "linked.png").symlink_to(secret)
            (self.media / "linked_directory").symlink_to(outside, target_is_directory=True)
            for path in (
                "linked.png", "linked_directory/secret.png", "../secret.png",
                "production_styles/../../secret.png", "production_styles/./assigned.png",
                "production_styles//assigned.png", "production_styles\\assigned.png",
            ):
                with self.subTest(path=path):
                    self.assertEqual(self.get_file(path).status_code, 404)

    def test_missing_files_and_directories_return_private_not_found(self):
        self.client.force_login(self.admin)
        for path in ("missing.png", "production_styles"):
            response = self.get_file(path)
            self.assertEqual(response.status_code, 404)
            self.assert_private(response)
            self.assertNotIn(str(self.media), response.content.decode())

    def test_financial_prefix_denials_match_existing_web_server_rules(self):
        for user in (self.user, self.employee, self.admin):
            self.client.force_login(user)
            for directory in ("financial_core", "accounting", "accounting_docs"):
                path = f"{directory}/private.png"
                self.write(path)
                response = self.get_file(path)
                self.assertEqual(response.status_code, 404)
                self.assert_private(response)

    def test_employee_order_scope_and_unrelated_media_access_are_preserved(self):
        self.client.force_login(self.employee)
        self.assert_download(self.other.style_image.name)
        self.assertEqual(self.get_file(self.order.style_image.name).status_code, 404)
        self.write("catalog/material.png")
        self.assert_download("catalog/material.png")
        self.client.force_login(self.admin)
        self.assert_download(self.order.style_image.name)
        self.assert_download(self.other.style_image.name)

    def test_lead_employee_can_still_download_shared_reference_from_visible_lead(self):
        lead = Lead.objects.create(account_brand="Owned shared lead", assigned_to=self.lead_employee)
        reference = self.reference("product_reference_images/lead-shared.png", self.other, lead=lead)
        self.client.force_login(self.lead_employee)
        self.assert_download(reference.image.name)
        # A Production-only employee still has no access to an unrelated order.
        self.client.force_login(self.employee)
        ProductReferenceImage.objects.filter(pk=reference.pk).update(production_order=self.order)
        self.assertEqual(self.get_file(reference.image.name).status_code, 404)

    def test_employee_active_attachments_are_downloads_with_sandbox_headers(self):
        self.client.force_login(self.employee)
        for suffix in ("html", "svg"):
            path = f"production_attachments/active.{suffix}"
            content = b"<script>window.BAD=true</script>"
            self.write(path, content)
            ProductionOrderAttachment.objects.create(order=self.other, file=path)
            response = self.assert_download(path, content)
            self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))

    def test_conditional_requests_recheck_assignment_and_do_not_issue_public_validators(self):
        response = self.get_file(self.order.style_image.name, HTTP_IF_NONE_MATCH="*")
        self.assertEqual(response.status_code, 200)
        self.assert_private(response)
        response.close()
        ProductionOrder.objects.filter(pk=self.order.pk).update(assigned_production_manager=None)
        self.assertEqual(self.get_file(self.order.style_image.name, HTTP_IF_NONE_MATCH="*").status_code, 404)

    def test_file_route_supports_head_and_rejects_mutations(self):
        response = self.client.head(self.url(self.order.style_image.name))
        self.assertEqual(response.status_code, 200)
        self.assert_private(response)
        self.assertEqual(b"".join(response.streaming_content), b"")
        self.assertEqual(self.client.post(self.url(self.order.style_image.name)).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(self.order.style_image.name)).status_code, 405)

    def test_existing_external_delivery_stays_available_without_production_bypass(self):
        path = "whatsapp_api/outbound/test-recipient/existing-delivery.png"
        self.write(path)
        self.client.logout()
        self.assert_download(path)
        # Even an unusual legacy filename is private when it belongs to an order.
        ProductionOrder.objects.filter(pk=self.order.pk).update(style_image=path)
        self.assertEqual(self.get_file(path).status_code, 302)
        self.client.force_login(self.user)
        self.assert_download(path)
        ProductionOrder.objects.filter(pk=self.order.pk).update(assigned_production_manager=None)
        self.assertEqual(self.get_file(path).status_code, 404)
