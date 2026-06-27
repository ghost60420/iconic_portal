from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from crm.audit_context import reset_current_actor, set_current_actor
from crm.models import (
    AutomationNotification,
    CRMAuditLog,
    CostingHeader,
    Customer,
    Invoice,
    Lead,
    Opportunity,
    ProductionOrder,
)
from crm.services.automation_engine import automation_dashboard_context
from crm.services.operations_dashboard import operations_dashboard_context
from crm.services.operations_notifications import sync_operations_notifications, visible_notifications
from crm.views_costing import _can_approve
from crm.views_operations import _group_notifications


class OperationsControlBase(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.ceo = self.User.objects.create_user("ops-ceo", password="test-pass")
        self.sales = self.User.objects.create_user("ops-sales", password="test-pass")
        self.sales_other = self.User.objects.create_user("ops-sales-other", password="test-pass")
        self.production = self.User.objects.create_user("ops-production", password="test-pass")
        self.accounts = self.User.objects.create_user("ops-accounts", password="test-pass")
        self.merchandising = self.User.objects.create_user("ops-merch", password="test-pass")
        self.regular = self.User.objects.create_user("ops-regular", password="test-pass")
        for name, user in (
            ("CEO", self.ceo),
            ("Sales", self.sales),
            ("Sales", self.sales_other),
            ("Production", self.production),
            ("Accounts", self.accounts),
            ("Merchandising", self.merchandising),
        ):
            Group.objects.get_or_create(name=name)[0].user_set.add(user)

        self.customer = Customer.objects.create(
            account_brand="Iconic Test Customer",
            contact_name="Operations Buyer",
            email="buyer@example.com",
            phone="6045550101",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Iconic Test Customer",
            contact_name="Operations Buyer",
            email="buyer@example.com",
            phone="6045550101",
            assigned_to=self.sales,
        )
        self.other_lead = Lead.objects.create(
            account_brand="Other Sales Customer",
            contact_name="Other Buyer",
            email="other@example.com",
            assigned_to=self.sales_other,
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            product_type="Activewear",
            product_category="Leggings",
            order_currency="CAD",
            order_value=Decimal("2500.00"),
        )


class DashboardPerformanceGuardTests(OperationsControlBase):
    def test_automation_sync_runs_once_within_dashboard_freshness_window(self):
        cache.clear()
        with patch("crm.services.automation_engine.sync_automation_engine") as sync_engine:
            sync_engine.return_value = {"created": 0, "error": ""}
            automation_dashboard_context(self.ceo)
            automation_dashboard_context(self.ceo)

        sync_engine.assert_called_once_with(created_by=self.ceo)


class NotificationCenterTests(OperationsControlBase):
    def test_quotation_submission_notifies_ceo_only(self):
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            status="approved",
            order_quantity=100,
            currency="CAD",
            manual_fob_per_piece=Decimal("25.00"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            costing.quotation_number = "QT-OPS-001"
            costing.quoted_by = self.sales
            costing.quoted_at = timezone.now()
            costing.save(update_fields=["quotation_number", "quoted_by", "quoted_at", "updated_at"])

        item = AutomationNotification.objects.get(
            notification_type="ceo_approval",
            assigned_user=self.ceo,
        )
        self.assertEqual(item.record_object_id, costing.pk)
        self.assertIn(item, visible_notifications(self.ceo))
        self.assertNotIn(item, visible_notifications(self.sales))

    def test_due_shipping_and_overdue_invoice_notifications(self):
        today = timezone.localdate()
        order = ProductionOrder.objects.create(
            title="Due Production",
            order_code="PO-OPS-DUE",
            customer=self.customer,
            qty_total=100,
            bulk_deadline=today + timedelta(days=2),
            operational_status="ready_to_ship",
        )
        order.operational_status = "ready_to_ship"
        order.save(update_fields=["operational_status", "updated_at"])
        Invoice.objects.create(
            invoice_number="INV-OPS-OVERDUE",
            customer=self.customer,
            issue_date=today - timedelta(days=20),
            due_date=today - timedelta(days=5),
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("0"),
            currency="CAD",
        )

        result = sync_operations_notifications(today=today, force=True)
        self.assertEqual(result["error"], "")
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="production_due",
                assigned_user=self.production,
                record_object_id=order.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="shipping",
                assigned_user=self.ceo,
                record_object_id=order.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="invoice_overdue",
                assigned_user=self.accounts,
            ).exists()
        )
        legacy = AutomationNotification.objects.create(
            source_key="crm-auto:invoice_overdue:999",
            rule_type="invoice",
            title="Legacy overdue duplicate",
        )
        self.assertNotIn(legacy, visible_notifications(self.ceo))

    def test_mark_read_and_mark_all_are_recipient_scoped(self):
        first = AutomationNotification.objects.create(
            source_key="test:ops:user:first",
            title="First",
            notification_type="general",
            assigned_user=self.sales,
        )
        other = AutomationNotification.objects.create(
            source_key="test:ops:user:other",
            title="Other",
            notification_type="general",
            assigned_user=self.sales_other,
        )
        client = Client()
        client.force_login(self.sales)
        list_response = client.get(reverse("notification_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.context["crm_header_unread_count"], 1)
        response = client.post(reverse("notification_mark_read", args=[first.pk]))
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(first.is_read)
        self.assertFalse(other.is_read)

        response = client.post(reverse("notification_mark_all_read"))
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertFalse(other.is_read)

    def test_notifications_are_grouped_with_icons_and_relative_age(self):
        fixed_now = timezone.make_aware(datetime(2026, 6, 27, 12, 0))
        items = []
        for index, (days, notification_type) in enumerate(
            ((0, "ceo_approval"), (1, "production_due"), (3, "shipping"), (10, "invoice_overdue")),
            start=1,
        ):
            item = AutomationNotification.objects.create(
                source_key=f"test:group:{index}",
                title=f"Grouped {index}",
                notification_type=notification_type,
                assigned_user=self.ceo,
            )
            AutomationNotification.objects.filter(pk=item.pk).update(created_at=fixed_now - timedelta(days=days))
            item.refresh_from_db()
            items.append(item)
        with patch("crm.views_operations.timezone.localdate", return_value=date(2026, 6, 27)):
            grouped = _group_notifications(items)
        self.assertEqual([label for label, _ in grouped], ["Today", "Yesterday", "This Week", "Older"])
        self.assertEqual(grouped[0][1][0].icon_label, "CEO Approval")
        self.assertEqual(grouped[1][1][0].icon_label, "Production")
        self.assertEqual(grouped[2][1][0].icon_label, "Shipping")
        self.assertEqual(grouped[3][1][0].icon_label, "Finance")

    def test_mark_selected_and_delete_read_are_recipient_scoped(self):
        own = AutomationNotification.objects.create(
            source_key="test:selected:own",
            title="Own",
            assigned_user=self.sales,
        )
        other = AutomationNotification.objects.create(
            source_key="test:selected:other",
            title="Other",
            assigned_user=self.sales_other,
        )
        client = Client()
        client.force_login(self.sales)
        response = client.post(
            reverse("notification_mark_selected_read"),
            {"notification_ids": [str(own.pk), str(other.pk)]},
        )
        self.assertEqual(response.status_code, 302)
        own.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertFalse(other.is_read)

        response = client.post(reverse("notification_delete_read"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AutomationNotification.objects.filter(pk=own.pk).exists())
        self.assertTrue(AutomationNotification.objects.filter(pk=other.pk).exists())


class AuditLogTests(OperationsControlBase):
    def test_create_and_field_update_capture_actor(self):
        token = set_current_actor(self.sales)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                lead = Lead.objects.create(account_brand="Audit Brand", assigned_to=self.sales)
            with self.captureOnCommitCallbacks(execute=True):
                lead.lead_status = "Contacted"
                lead.save(update_fields=["lead_status"])
        finally:
            reset_current_actor(token)

        created = CRMAuditLog.objects.get(
            actor=self.sales,
            module="leads",
            record_id=str(lead.pk),
            action_type="created",
        )
        self.assertEqual(created.previous_value, "")
        self.assertEqual(created.new_value, lead.lead_id)
        update = CRMAuditLog.objects.get(
            actor=self.sales,
            module="leads",
            record_id=str(lead.pk),
            field_name="lead_status",
        )
        self.assertEqual(update.previous_value, "New")
        self.assertEqual(update.new_value, "Contacted")

    def test_delete_audit_keeps_record_label_as_old_value(self):
        token = set_current_actor(self.sales)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                lead = Lead.objects.create(account_brand="Deleted Audit Brand", assigned_to=self.sales)
            lead_id = lead.lead_id
            record_id = str(lead.pk)
            with self.captureOnCommitCallbacks(execute=True):
                lead.delete()
        finally:
            reset_current_actor(token)

        deleted = CRMAuditLog.objects.get(
            actor=self.sales,
            module="leads",
            record_id=record_id,
            action_type="deleted",
        )
        self.assertEqual(deleted.previous_value, lead_id)
        self.assertEqual(deleted.new_value, "")

    def test_production_audit_uses_order_number_not_related_lead_id(self):
        token = set_current_actor(self.production)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                order = ProductionOrder.objects.create(
                    order_code="PO-AUDIT-LABEL",
                    title="Audit label order",
                    lead=self.lead,
                )
        finally:
            reset_current_actor(token)
        row = CRMAuditLog.objects.filter(module="production", record_id=str(order.pk)).first()
        self.assertEqual(row.record_label, "PO-AUDIT-LABEL")

    def test_audit_failure_does_not_break_record_save(self):
        token = set_current_actor(self.sales)
        try:
            with patch("crm.services.audit_log.CRMAuditLog.objects.bulk_create", side_effect=RuntimeError("audit unavailable")):
                with self.captureOnCommitCallbacks(execute=True):
                    lead = Lead.objects.create(account_brand="Save Still Works")
        finally:
            reset_current_actor(token)
        self.assertTrue(Lead.objects.filter(pk=lead.pk).exists())

    def test_audit_page_is_ceo_only(self):
        client = Client()
        client.force_login(self.ceo)
        self.assertEqual(client.get(reverse("crm_audit_log")).status_code, 200)
        client.force_login(self.regular)
        self.assertEqual(client.get(reverse("crm_audit_log")).status_code, 403)

    def test_audit_exports_csv_and_excel_with_old_and_new_values(self):
        CRMAuditLog.objects.create(
            actor=self.ceo,
            module="quotations",
            record_id="42",
            record_label="Q000124",
            action_type="updated",
            field_name="selling_price",
            previous_value="CAD 15.00",
            new_value="CAD 16.50",
        )
        client = Client()
        client.force_login(self.ceo)
        csv_response = client.get(reverse("crm_audit_log"), {"export": "csv", "record_id": "42"})
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertIn("CAD 15.00", csv_response.content.decode())
        self.assertIn("CAD 16.50", csv_response.content.decode())

        excel_response = client.get(reverse("crm_audit_log"), {"export": "excel", "record_id": "42"})
        self.assertEqual(excel_response.status_code, 200)
        workbook = load_workbook(BytesIO(excel_response.content), read_only=True)
        values = list(workbook["CRM Audit Log"].values)
        self.assertEqual(values[1][6:8], ("CAD 15.00", "CAD 16.50"))

    def test_audit_page_displays_separate_old_and_new_values(self):
        CRMAuditLog.objects.create(
            module="quotations",
            record_id="42",
            action_type="updated",
            field_name="selling_price",
            previous_value="CAD 15.00",
            new_value="CAD 16.50",
        )
        client = Client()
        client.force_login(self.ceo)
        response = client.get(reverse("crm_audit_log"))
        self.assertContains(response, "Old")
        self.assertContains(response, "New")
        self.assertContains(response, "CAD 15.00")
        self.assertContains(response, "CAD 16.50")


class GlobalSearchPermissionTests(OperationsControlBase):
    def setUp(self):
        super().setUp()
        self.order = ProductionOrder.objects.create(
            title="Search Production",
            order_code="PO-SEARCH-001",
            customer=self.customer,
            client_name_snapshot="Iconic Test Customer",
        )
        self.invoice = Invoice.objects.create(
            invoice_number="INV-SEARCH-001",
            customer=self.customer,
            total_amount=Decimal("1000.00"),
            currency="CAD",
        )

    def test_sales_search_is_scoped_to_own_pipeline(self):
        client = Client()
        client.force_login(self.sales)
        response = client.get(reverse("global_search"), {"q": "Customer"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.lead.lead_id)
        self.assertNotContains(response, self.other_lead.lead_id)
        self.assertNotContains(response, self.order.order_code)
        self.assertNotContains(response, self.invoice.invoice_number)

    def test_production_and_accounts_results_are_module_scoped(self):
        client = Client()
        client.force_login(self.production)
        response = client.get(reverse("global_search"), {"q": "SEARCH"})
        self.assertContains(response, self.order.order_code)
        self.assertNotContains(response, self.invoice.invoice_number)

        client.force_login(self.accounts)
        response = client.get(reverse("global_search"), {"q": "SEARCH"})
        self.assertContains(response, self.invoice.invoice_number)
        self.assertNotContains(response, self.order.order_code)

    def test_search_requires_two_characters(self):
        client = Client()
        client.force_login(self.ceo)
        response = client.get(reverse("global_search"), {"q": "I"})
        self.assertContains(response, "Enter at least two characters")

    def test_instant_suggestions_are_permission_scoped_and_limited(self):
        for index in range(12):
            Customer.objects.create(account_brand=f"Limit Brand {index:02d}")
        client = Client()
        client.force_login(self.ceo)
        response = client.get(reverse("global_search_suggestions"), {"q": "Limit Brand"})
        self.assertEqual(response.status_code, 200)
        groups = {group["label"]: group["rows"] for group in response.json()["groups"]}
        self.assertEqual(len(groups["Customers"]), 10)
        self.assertNotIn("Opportunities", groups)

        client.force_login(self.production)
        response = client.get(reverse("global_search_suggestions"), {"q": "SEARCH"})
        labels = {group["label"] for group in response.json()["groups"]}
        self.assertIn("Production", labels)
        self.assertNotIn("Invoices", labels)


class DashboardAndRoleTests(OperationsControlBase):
    def test_dashboard_service_has_bounded_queries(self):
        with CaptureQueriesContext(connection) as queries:
            context = operations_dashboard_context(self.ceo)
            self.assertIn("operations_recent_activity", context)
        self.assertLessEqual(len(queries), 12)

    def test_dashboard_renders_operations_sections(self):
        CRMAuditLog.objects.create(
            module="leads",
            record_id=str(self.lead.pk),
            record_label=self.lead.lead_id,
            action_type="created",
            actor=None,
        )
        client = Client()
        client.force_login(self.ceo)
        response = client.get(reverse("main_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Activity")
        self.assertContains(response, "Today’s Tasks")
        self.assertContains(response, "Upcoming Deliveries")
        self.assertContains(response, "Pending Approvals")
        self.assertContains(response, "System")

    def test_dashboard_has_clickable_metric_cards(self):
        today = timezone.localdate()
        ProductionOrder.objects.create(
            title="Due today",
            order_code="PO-DUE-TODAY",
            bulk_deadline=today,
        )
        ProductionOrder.objects.create(
            title="Late",
            order_code="PO-LATE",
            bulk_deadline=today - timedelta(days=1),
        )
        ready_order = ProductionOrder.objects.create(
            title="Ready",
            order_code="PO-READY",
        )
        ProductionOrder.objects.filter(pk=ready_order.pk).update(operational_status="ready_to_ship")
        Invoice.objects.create(
            invoice_number="INV-LATE",
            customer=self.customer,
            due_date=today - timedelta(days=1),
            total_amount=Decimal("100.00"),
            paid_amount=Decimal("0"),
        )
        context = operations_dashboard_context(self.ceo)
        cards = {card["label"]: card for card in context["operations_metric_cards"]}
        self.assertEqual(cards["Production Due Today"]["count"], 1)
        self.assertEqual(cards["Late Production Orders"]["count"], 1)
        self.assertEqual(cards["Ready to Ship"]["count"], 1)
        self.assertEqual(cards["Invoices Overdue"]["count"], 1)
        self.assertTrue(all(card["url"] for card in cards.values()))

    def test_recent_activity_is_narrative_and_limited_to_25(self):
        for index in range(26):
            CRMAuditLog.objects.create(
                actor=self.ceo,
                module="quotations",
                record_id=str(index),
                record_label=f"Q{index:06d}",
                action_type="approved",
            )
        context = operations_dashboard_context(self.ceo)
        rows = context["operations_recent_activity"]
        self.assertEqual(len(rows), 25)
        self.assertIn("ops-ceo approved Quotation", rows[0]["sentence"])
        self.assertEqual(rows[0]["actor_initials"], "O")

    def test_role_setup_is_additive_and_creates_all_groups(self):
        call_command("setup_operations_roles", verbosity=0)
        self.assertEqual(
            set(Group.objects.filter(name__in=["CEO", "Sales", "Production", "Accounts", "Merchandising"]).values_list("name", flat=True)),
            {"CEO", "Sales", "Production", "Accounts", "Merchandising"},
        )
        self.assertTrue(self.ceo.is_active)

    def test_sales_cannot_approve_and_ceo_can(self):
        self.assertFalse(_can_approve(self.sales))
        self.assertTrue(_can_approve(self.ceo))

    def test_sales_is_blocked_from_production_while_production_user_can_view(self):
        client = Client()
        client.force_login(self.sales)
        self.assertEqual(client.get(reverse("production_list")).status_code, 403)
        client.force_login(self.production)
        self.assertEqual(client.get(reverse("production_list")).status_code, 200)

    def test_sales_pipeline_list_is_scoped_to_assigned_leads(self):
        client = Client()
        client.force_login(self.sales)
        response = client.get(reverse("leads_list"))
        self.assertContains(response, self.lead.lead_id)
        self.assertNotContains(response, self.other_lead.lead_id)

    def test_accounts_and_merchandising_restrictions(self):
        client = Client()
        client.force_login(self.accounts)
        self.assertEqual(client.get(reverse("production_list")).status_code, 403)
        self.assertEqual(client.get(reverse("invoice_list")).status_code, 200)

        client.force_login(self.merchandising)
        self.assertEqual(client.get(reverse("production_list")).status_code, 200)
        self.assertEqual(client.get(reverse("invoice_list")).status_code, 403)

    def test_ceo_keeps_full_view_access_without_changing_legacy_team_field(self):
        client = Client()
        client.force_login(self.ceo)
        self.assertEqual(client.get(reverse("accounting_ca_master")).status_code, 200)

        client.force_login(self.accounts)
        self.assertEqual(client.get(reverse("accounting_ca_master")).status_code, 403)

    def test_operations_queues_enforce_module_permissions(self):
        ProductionOrder.objects.create(
            title="Due queue",
            order_code="PO-QUEUE",
            bulk_deadline=timezone.localdate(),
        )
        client = Client()
        client.force_login(self.production)
        response = client.get(reverse("operations_queue", args=["production-due-today"]))
        self.assertContains(response, "PO-QUEUE")
        client.force_login(self.sales)
        self.assertEqual(client.get(reverse("operations_queue", args=["production-due-today"])).status_code, 403)

    def test_role_management_is_ceo_only_and_manages_members(self):
        client = Client()
        client.force_login(self.regular)
        self.assertEqual(client.get(reverse("role_management")).status_code, 403)

        client.force_login(self.ceo)
        response = client.post(reverse("role_management"), {"action": "create_role", "role_name": "Regional Sales"})
        self.assertEqual(response.status_code, 302)
        role = Group.objects.get(name="Regional Sales")
        response = client.post(
            reverse("role_management"),
            {"action": "assign_user", "role_id": role.pk, "user_id": self.regular.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(role.user_set.filter(pk=self.regular.pk).exists())
        response = client.post(
            reverse("role_management"),
            {"action": "remove_user", "role_id": role.pk, "user_id": self.regular.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(role.user_set.filter(pk=self.regular.pk).exists())

    def test_role_management_prevents_self_removal_from_ceo(self):
        client = Client()
        client.force_login(self.ceo)
        ceo_group = Group.objects.get(name="CEO")
        client.post(
            reverse("role_management"),
            {"action": "remove_user", "role_id": ceo_group.pk, "user_id": self.ceo.pk},
        )
        self.assertTrue(ceo_group.user_set.filter(pk=self.ceo.pk).exists())
