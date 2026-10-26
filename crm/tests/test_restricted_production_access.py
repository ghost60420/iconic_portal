"""Assigned-only production security regressions using isolated test records."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse

from crm.models import (
    CRMAuditLog, Customer, Invoice, InvoicePayment, JournalEntry,
    ProductionOrder, ProductionStage, SystemActivityLog,
)
from crm.models_access import UserAccess
from crm.production_forms import ProductionOrderForm
from crm.services.restricted_production import RESTRICTED_PRODUCTION_GROUP


def access_flags():
    return [field.name for field in UserAccess._meta.fields if field.name.startswith("can_")]


def account_state(user):
    return {
        "user": get_user_model().objects.filter(pk=user.pk).values().get(),
        "access": UserAccess.objects.filter(user=user).values().get(),
        "groups": set(user.groups.values_list("pk", flat=True)),
        "permissions": set(user.user_permissions.values_list("pk", flat=True)),
    }


class RestrictTarangoCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.tarango = users.objects.create_superuser(
            pk=16, username="Tarango", first_name="Tarango", last_name="Hossain",
            email="tarango-command@example.test", password="test-only-password",
        )
        cls.ceo = users.objects.create_superuser(username="restriction-approver", password="test")
        cls.employee = users.objects.create_user(username="unchanged-employee", is_staff=True)
        cls.production_group = Group.objects.get_or_create(name="Production")[0]
        cls.tarango.groups.add(cls.production_group, Group.objects.get_or_create(name="Admin")[0])
        cls.employee.groups.add(cls.production_group)
        permission = Permission.objects.get(codename="change_productionorder", content_type__app_label="crm")
        cls.tarango.user_permissions.add(permission)
        cls.employee.user_permissions.add(permission)
        cls.order = ProductionOrder.objects.create(title="Existing unassigned order")
        cls.assigned_order = ProductionOrder.objects.create(
            title="Existing employee assignment", assigned_production_manager=cls.employee,
        )
        cls.history = CRMAuditLog.objects.create(
            module="production", record_id=str(cls.order.pk), field_name="notes",
            previous_value="Before", new_value="Historical note", actor=cls.employee,
        )

    def apply(self, **options):
        return call_command(
            "restrict_tarango_production", apply=True, actor=self.ceo.pk,
            stdout=StringIO(), **options,
        )

    def test_dry_run_does_not_change_any_account_or_order(self):
        before = account_state(self.tarango)
        orders = list(ProductionOrder.objects.values())
        logs = SystemActivityLog.objects.count()
        call_command("restrict_tarango_production", stdout=StringIO())
        self.assertEqual(account_state(self.tarango), before)
        self.assertEqual(list(ProductionOrder.objects.values()), orders)
        self.assertEqual(SystemActivityLog.objects.count(), logs)
        self.assertFalse(Group.objects.filter(name=RESTRICTED_PRODUCTION_GROUP).exists())

    def test_apply_removes_unrestricted_permissions_only_from_verified_account(self):
        employee_before = account_state(self.employee)
        ceo_before = account_state(self.ceo)
        orders_before = list(ProductionOrder.objects.values())
        stages_before = list(ProductionStage.objects.values())
        history_before = CRMAuditLog.objects.filter(pk=self.history.pk).values().get()
        self.apply()
        self.tarango.refresh_from_db()
        self.assertFalse(self.tarango.is_superuser)
        self.assertFalse(self.tarango.is_staff)
        self.assertTrue(self.tarango.is_active)
        self.assertEqual(set(self.tarango.groups.values_list("name", flat=True)), {RESTRICTED_PRODUCTION_GROUP})
        self.assertFalse(self.tarango.user_permissions.exists())
        self.assertFalse(Group.objects.get(name=RESTRICTED_PRODUCTION_GROUP).permissions.exists())
        access = UserAccess.objects.get(user=self.tarango)
        self.assertEqual({flag for flag in access_flags() if getattr(access, flag)}, {"can_production"})
        self.assertEqual(account_state(self.employee), employee_before)
        self.assertEqual(account_state(self.ceo), ceo_before)
        self.assertEqual(list(ProductionOrder.objects.values()), orders_before)
        self.assertEqual(list(ProductionStage.objects.values()), stages_before)
        self.assertEqual(CRMAuditLog.objects.filter(pk=self.history.pk).values().get(), history_before)
        self.assertTrue(SystemActivityLog.objects.filter(object_id="16", actor=self.ceo).exists())

    def test_repeated_apply_preserves_permissions_and_assignments(self):
        self.apply()
        before = account_state(self.tarango)
        before["access"].pop("updated_at")
        orders = list(ProductionOrder.objects.values())
        self.apply()
        after = account_state(self.tarango)
        after["access"].pop("updated_at")
        self.assertEqual(after, before)
        self.assertEqual(list(ProductionOrder.objects.values()), orders)
        self.assertEqual(Group.objects.filter(name=RESTRICTED_PRODUCTION_GROUP).count(), 1)

    def test_id_with_wrong_identity_is_rejected_without_modification(self):
        for change in ({"username": "AnotherPerson"}, {"first_name": "Another"}, {"last_name": "Person"}):
            with self.subTest(change=change):
                get_user_model().objects.filter(pk=16).update(
                    username="Tarango", first_name="Tarango", last_name="Hossain",
                )
                get_user_model().objects.filter(pk=16).update(**change)
                before = account_state(self.tarango)
                with self.assertRaises(CommandError):
                    self.apply()
                self.assertEqual(account_state(self.tarango), before)
        self.assertFalse(Group.objects.filter(name=RESTRICTED_PRODUCTION_GROUP).exists())

    def test_matching_name_at_another_id_is_never_modified(self):
        get_user_model().objects.filter(pk=16).update(username="DifferentAccount", first_name="Different")
        other = get_user_model().objects.create_superuser(
            username="Tarango", first_name="Tarango", last_name="Hossain", password="test",
        )
        before = account_state(other)
        with self.assertRaises(CommandError):
            self.apply()
        self.assertEqual(account_state(other), before)

    def test_apply_requires_an_authorized_actor(self):
        before = account_state(self.tarango)
        for actor in (None, self.employee.pk, self.tarango.pk, 999999):
            with self.subTest(actor=actor), self.assertRaises(CommandError):
                call_command("restrict_tarango_production", apply=True, actor=actor, stdout=StringIO())
        self.assertEqual(account_state(self.tarango), before)

    def test_existing_marker_cannot_implicitly_change_another_account(self):
        group = Group.objects.create(name=RESTRICTED_PRODUCTION_GROUP)
        self.employee.groups.add(group)
        before = account_state(self.tarango)
        with self.assertRaises(CommandError):
            self.apply()
        self.assertEqual(account_state(self.tarango), before)

    def test_existing_login_session_loses_broad_access_after_activation(self):
        self.assertTrue(self.client.login(username="Tarango", password="test-only-password"))
        self.apply()
        self.assertEqual(self.client.get(reverse("customers_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("financial_core_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("production_list")).status_code, 200)


class RestrictedProductionAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.tarango = users.objects.create_user(
            pk=16, username="Tarango", first_name="Tarango", last_name="Hossain",
            password="test-only-password",
        )
        cls.tarango.groups.add(Group.objects.get_or_create(name=RESTRICTED_PRODUCTION_GROUP)[0])
        UserAccess.objects.filter(user=cls.tarango).update(**{flag: flag == "can_production" for flag in access_flags()})
        cls.ceo = users.objects.create_superuser(username="production-ceo", password="test")
        cls.employee = users.objects.create_user(username="production-employee")
        cls.employee.groups.add(Group.objects.get_or_create(name="Production")[0])
        cls.customer = Customer.objects.create(
            account_brand="Private Client", contact_name="Private Contact",
            email="private-client@example.test", phone="+1-555-987-6543",
        )
        cls.order = ProductionOrder.objects.create(
            title="Assigned manufacture", customer=cls.customer,
            assigned_production_manager=cls.tarango,
        )
        cls.other_order = ProductionOrder.objects.create(
            title="Other employee manufacture", assigned_production_manager=cls.employee,
        )
        cls.unassigned_order = ProductionOrder.objects.create(title="Unassigned manufacture")

    def setUp(self):
        self.client.force_login(self.tarango)

    def stage(self, key="printing", order=None):
        return (order or self.order).stages.get(stage_key=key)

    def edit(self, stage, **changes):
        data = {
            "version": stage.updated_at.isoformat(), "status": "in_progress",
            "planned_start": "2026-11-01", "planned_end": "2026-11-02",
            "notes": "Assigned factory progress",
        }
        data.update(changes)
        return self.client.post(reverse("production_stage_edit", args=[stage.pk]), data, HTTP_ACCEPT="application/json")

    def test_only_assigned_orders_appear_in_list_and_search(self):
        for query in ({}, {"q": "manufacture"}, {"q": "Other employee"}, {"q": "Unassigned"}):
            with self.subTest(query=query):
                response = self.client.get(reverse("production_list"), query)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.other_order.title)
                self.assertNotContains(response, self.unassigned_order.title)
        self.assertContains(self.client.get(reverse("production_list")), self.order.order_code)

    def test_direct_unassigned_detail_and_stage_urls_are_denied(self):
        for order in (self.other_order, self.unassigned_order):
            stage = self.stage(order=order)
            for route, pk in (("production_detail", order.pk), ("production_stage_edit", stage.pk), ("production_stage_click", stage.pk)):
                with self.subTest(order=order.pk, route=route):
                    self.assertEqual(self.client.get(reverse(route, args=[pk])).status_code, 403 if route == "production_stage_click" else 404)
                    data = {"action": "add_comment", "comment_text": "Blocked"} if route == "production_detail" else {}
                    self.assertEqual(self.client.post(reverse(route, args=[pk]), data).status_code, 404)
            stage.refresh_from_db()
            self.assertEqual(stage.status, "planned")

    def test_direct_restricted_module_reads_and_writes_are_denied(self):
        routes = [
            ("customers_list", []), ("customer_detail", [self.customer.pk]),
            ("leads_list", []), ("opportunities_list", []),
            ("shipment_list", []), ("shipment_detail", [1]),
            ("shipping_add_for_order", [self.order.pk]),
            ("inventory_list", []), ("inventory_detail", [1]),
            ("financial_core_dashboard", []), ("finance_operations_center", []),
            ("accounting_list_export_csv", []), ("accounting_list_export_xlsx", []),
            ("invoice_list", []), ("invoice_view", [1]), ("invoice_pdf", [1]),
            ("invoice_payment_add", [1]), ("cost_sheet_list", []),
            ("quick_costing_client_quotation", [1]), ("quick_costing_export_pdf", [1]),
            ("global_search", []), ("global_search_suggestions", []),
            ("chatter_feed", []), ("employee_list", []), ("employee_edit", [self.ceo.pk]),
            ("notification_list", []), ("calendar_list", []), ("products_list", []),
            ("email_sync_dashboard", []), ("ceo_dashboard", []),
        ]
        paths = [reverse(name, args=args) for name, args in routes]
        paths += ["/admin/", "/admin/auth/user/16/change/", "/marketing/", "/whatsapp/"]
        for path in paths:
            for method in (self.client.get, self.client.post):
                with self.subTest(path=path, method=method.__name__):
                    self.assertEqual(method(path, {"q": "Private Client"}).status_code, 403)

    def test_production_administration_exports_and_shipping_stage_are_denied(self):
        for name, args in (
            ("production_add", []), ("production_edit", [self.order.pk]),
            ("production_archive", [self.order.pk]), ("production_delete", [self.order.pk]),
            ("production_next_stage", [self.order.pk]), ("production_dpr", [self.order.pk]),
            ("production_ai_help", [self.order.pk]), ("ai_production_suggest", [self.order.pk]),
            ("production_order_sheet_pdf", [self.order.pk]), ("production_packing_list_pdf", [self.order.pk]),
            ("production_attachment_add", [self.order.pk]),
            ("production_attachment_delete", [self.order.pk, 1]),
            ("production_from_opportunity", [1]),
        ):
            for method in (self.client.get, self.client.post):
                with self.subTest(route=name, method=method.__name__):
                    self.assertEqual(method(reverse(name, args=args)).status_code, 403)
        shipping = self.stage("shipping")
        for name in ("production_stage_edit", "production_stage_click"):
            for method in (self.client.get, self.client.post):
                with self.subTest(route=name, method=method.__name__):
                    self.assertIn(method(reverse(name, args=[shipping.pk])).status_code, (403, 404))
        shipping.refresh_from_db()
        self.assertEqual(shipping.status, "planned")

    def test_restriction_overrides_accidentally_restored_superuser_groups_and_flags(self):
        get_user_model().objects.filter(pk=self.tarango.pk).update(is_superuser=True, is_staff=True)
        self.tarango.groups.add(Group.objects.get_or_create(name="CEO")[0], Group.objects.get_or_create(name="Production")[0])
        UserAccess.objects.filter(user=self.tarango).update(**{flag: True for flag in access_flags()})
        self.assertEqual(self.client.get(reverse("customer_detail", args=[self.customer.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("financial_core_dashboard")).status_code, 403)
        self.assertEqual(self.client.get("/admin/").status_code, 403)
        self.assertEqual(self.client.get(reverse("production_detail", args=[self.other_order.pk])).status_code, 404)
        self.assertEqual(self.edit(self.stage(order=self.other_order)).status_code, 404)

    def test_assigned_stage_dates_notes_qc_and_audit_work(self):
        for key in ("printing", "qc"):
            with self.subTest(stage=key):
                stage = self.stage(key)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.edit(stage, status="done")
                self.assertEqual(response.status_code, 200)
                stage.refresh_from_db()
                self.assertEqual(stage.status, "done")
                self.assertEqual(str(stage.planned_start), "2026-11-01")
                self.assertEqual(str(stage.planned_end), "2026-11-02")
                self.assertEqual(stage.notes, "Assigned factory progress")
                self.assertIsNotNone(stage.actual_started_at)
                self.assertIsNotNone(stage.actual_completed_at)
                self.assertTrue(CRMAuditLog.objects.filter(
                    module="production", record_id=str(self.order.pk), actor=self.tarango,
                    field_name=f"stage[{stage.pk}].status", previous_value="planned", new_value="done",
                ).exists())

    def test_quick_update_checks_concurrent_version(self):
        stage = self.stage()
        data = {"version": stage.updated_at.isoformat(), "expected_status": "planned"}
        url = reverse("production_stage_click", args=[stage.pk])
        self.assertEqual(self.client.post(url, data, HTTP_ACCEPT="application/json").status_code, 200)
        self.assertEqual(self.client.post(url, data, HTTP_ACCEPT="application/json").status_code, 409)
        stage.refresh_from_db()
        self.assertEqual(stage.status, "in_progress")

    def test_stage_post_cannot_modify_assignment_finances_or_historical_timestamps(self):
        before_order = ProductionOrder.objects.filter(pk=self.order.pk).values().get()
        stage = self.stage()
        response = self.edit(
            stage, status="planned", assigned_production_manager=self.employee.pk,
            approved_selling_price="99999", material_other_cost_bdt="99999",
            order=self.other_order.pk, stage_key="shipping", actual_start="2000-01-01",
            actual_end="2000-01-02", actual_started_at="2000-01-01T00:00:00Z",
        )
        self.assertEqual(response.status_code, 200)
        stage.refresh_from_db()
        self.assertEqual(stage.order_id, self.order.pk)
        self.assertEqual(stage.stage_key, "printing")
        self.assertIsNone(stage.actual_start)
        self.assertIsNone(stage.actual_end)
        self.assertIsNone(stage.actual_started_at)
        after_order = ProductionOrder.objects.filter(pk=self.order.pk).values().get()
        for field in ("assigned_production_manager_id", "approved_selling_price", "material_other_cost_bdt"):
            self.assertEqual(after_order[field], before_order[field])

    def test_revoked_assignment_takes_effect_in_existing_session(self):
        stage = self.stage()
        self.assertEqual(self.client.get(reverse("production_detail", args=[self.order.pk])).status_code, 200)
        ProductionOrder.objects.filter(pk=self.order.pk).update(assigned_production_manager=self.employee)
        self.assertEqual(self.client.get(reverse("production_detail", args=[self.order.pk])).status_code, 404)
        self.assertEqual(self.edit(stage).status_code, 404)
        self.assertNotContains(self.client.get(reverse("production_list")), self.order.title)

    def test_root_and_dashboard_redirect_to_assigned_production(self):
        for name in ("home", "main_dashboard"):
            self.assertRedirects(self.client.get(reverse(name)), reverse("production_list"), fetch_redirect_response=False)

    def test_csrf_is_still_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.tarango)
        self.assertEqual(client.post(reverse("production_stage_edit", args=[self.stage().pk]), {}).status_code, 403)

    def test_anonymous_stage_changes_are_rejected(self):
        self.client.logout()
        self.assertEqual(self.edit(self.stage()).status_code, 302)
        self.assertEqual(self.stage().status, "planned")

    def test_existing_employee_scope_shipping_and_customer_access_are_preserved(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.edit(self.stage(order=self.other_order)).status_code, 200)
        self.assertEqual(self.edit(self.stage()).status_code, 404)
        self.assertEqual(self.client.get(reverse("shipment_list")).status_code, 200)
        self.client.force_login(self.ceo)
        self.assertEqual(self.client.get(reverse("customer_detail", args=[self.customer.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("production_detail", args=[self.order.pk])).status_code, 200)
        self.assertEqual(self.edit(self.stage("shipping")).status_code, 200)

    def test_existing_assignment_field_accepts_restricted_user(self):
        form = ProductionOrderForm(instance=self.other_order)
        field = form.fields["assigned_production_manager"]
        self.assertEqual(field.clean(self.tarango.pk), self.tarango)

    def test_restriction_marker_does_not_change_any_other_account(self):
        self.employee.groups.add(Group.objects.get(name=RESTRICTED_PRODUCTION_GROUP))
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse("shipment_list")).status_code, 200)
        self.assertEqual(self.edit(self.stage(order=self.other_order)).status_code, 200)

    def test_updates_do_not_change_other_orders_or_create_financial_records(self):
        other_before = ProductionOrder.objects.filter(pk=self.other_order.pk).values().get()
        other_stages_before = list(self.other_order.stages.values())
        financial_models = (Invoice, InvoicePayment, JournalEntry)
        finances_before = {model: list(model.objects.values()) for model in financial_models}
        counts_before = (ProductionOrder.objects.count(), ProductionStage.objects.count())
        self.assertEqual(self.edit(self.stage()).status_code, 200)
        self.assertEqual(ProductionOrder.objects.filter(pk=self.other_order.pk).values().get(), other_before)
        self.assertEqual(list(self.other_order.stages.values()), other_stages_before)
        self.assertEqual((ProductionOrder.objects.count(), ProductionStage.objects.count()), counts_before)
        for model in financial_models:
            self.assertEqual(list(model.objects.values()), finances_before[model])
