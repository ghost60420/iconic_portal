from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from crm.audit_context import reset_current_actor, set_current_actor
from crm.context_processors import operations_header
from crm.models import (
    AutomationNotification,
    CRMAuditLog,
    CostingHeader,
    Customer,
    Invoice,
    Lead,
    LeadComment,
    LeadTask,
    Opportunity,
    ProductionOrder,
    Shipment,
)
from crm.models_access import UserAccess
from crm.services.automation_engine import automation_dashboard_context
from crm.services.operations_dashboard import operations_dashboard_context
from crm.services.operations_notifications import sync_operations_notifications, visible_notifications
from crm.views_costing import _can_approve
from crm.views_operations import _group_notifications


class OperationsControlBase(TestCase):
    def setUp(self):
        cache.clear()
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

    def test_only_approved_periodic_notifications_are_created(self):
        today = timezone.localdate()
        sample_order = ProductionOrder.objects.create(
            title="Sample Due",
            order_code="PO-OPS-SAMPLE",
            customer=self.customer,
            qty_total=100,
            sample_deadline=today,
        )
        overdue_order = ProductionOrder.objects.create(
            title="Overdue Production",
            order_code="PO-OPS-OVERDUE",
            customer=self.customer,
            qty_total=100,
            bulk_deadline=today - timedelta(days=2),
        )
        future_order = ProductionOrder.objects.create(
            title="Future Production",
            order_code="PO-OPS-FUTURE",
            bulk_deadline=today + timedelta(days=2),
            operational_status="ready_to_ship",
        )
        due_shipment = Shipment.objects.create(
            order=sample_order,
            ship_date=today,
            status="planned",
        )
        delayed_shipment = Shipment.objects.create(
            order=overdue_order,
            ship_date=today - timedelta(days=1),
            status="planned",
        )
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
                notification_type="sample_due",
                assigned_user=self.production,
                record_object_id=sample_order.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="production_due",
                assigned_user=self.production,
                record_object_id=overdue_order.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="shipment_due",
                assigned_user=self.ceo,
                record_object_id=due_shipment.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="shipment_delayed",
                assigned_user=self.production,
                record_object_id=delayed_shipment.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="invoice_overdue",
                assigned_user=self.accounts,
            ).exists()
        )
        self.assertFalse(
            AutomationNotification.objects.filter(record_object_id=future_order.pk).exists()
        )
        self.assertFalse(
            AutomationNotification.objects.filter(source_key__contains="ready_to_ship").exists()
        )
        first_count = AutomationNotification.objects.filter(source_key__startswith="operations:").count()
        sync_operations_notifications(today=today, force=True)
        self.assertEqual(
            AutomationNotification.objects.filter(source_key__startswith="operations:").count(),
            first_count,
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
            ((0, "ceo_approval"), (1, "production_due"), (3, "shipment_due"), (10, "invoice_overdue")),
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
        self.assertEqual(grouped[0][1][0].icon_symbol, "✔")
        self.assertEqual(grouped[1][1][0].icon_symbol, "🏭")
        self.assertEqual(grouped[2][1][0].icon_symbol, "🚚")
        self.assertEqual(grouped[3][1][0].icon_symbol, "💰")

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

        response = client.post(
            reverse("notification_delete_read"),
            {"notification_ids": [str(own.pk), str(other.pk)]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AutomationNotification.objects.filter(pk=own.pk).exists())
        self.assertTrue(AutomationNotification.objects.filter(pk=other.pk).exists())

    def test_priority_sort_recent_window_and_older_history(self):
        self.assertEqual(
            [value for value, _label in AutomationNotification.PRIORITY_CHOICES],
            ["critical", "high", "normal", "information"],
        )
        now = timezone.now()
        rows = []
        for priority in ("information", "normal", "high", "critical"):
            row = AutomationNotification.objects.create(
                source_key=f"test:priority:{priority}",
                title=f"{priority.title()} notice",
                priority=priority,
                assigned_user=self.ceo,
            )
            rows.append(row)
        old = AutomationNotification.objects.create(
            source_key="test:priority:old",
            title="Historical notice",
            priority="critical",
            assigned_user=self.ceo,
        )
        AutomationNotification.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=40))
        client = Client()
        client.force_login(self.ceo)
        response = client.get(reverse("notification_list"))
        visible = [item for _, items in response.context["notification_groups"] for item in items]
        self.assertEqual([item.priority for item in visible], ["critical", "high", "normal", "information"])
        self.assertTrue(response.context["has_older_notifications"])

        older_response = client.get(reverse("notification_list"), {"older": "1"})
        self.assertContains(older_response, "Historical notice")
        self.assertTrue(AutomationNotification.objects.filter(pk=old.pk).exists())

    def test_delete_visible_read_never_deletes_unread(self):
        read = AutomationNotification.objects.create(
            source_key="test:delete:read",
            title="Read",
            assigned_user=self.sales,
            is_read=True,
        )
        unread = AutomationNotification.objects.create(
            source_key="test:delete:unread",
            title="Unread",
            assigned_user=self.sales,
            is_read=False,
        )
        client = Client()
        client.force_login(self.sales)
        client.post(
            reverse("notification_delete_read"),
            {"notification_ids": [str(read.pk), str(unread.pk)]},
        )
        self.assertFalse(AutomationNotification.objects.filter(pk=read.pk).exists())
        self.assertTrue(AutomationNotification.objects.filter(pk=unread.pk).exists())

    def test_decisions_production_tasks_and_owner_comments_notify(self):
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            status="approved",
            quotation_number="QT-EVENT-001",
            quoted_by=self.sales,
        )
        with self.captureOnCommitCallbacks(execute=True):
            costing.quotation_status = CostingHeader.QUOTATION_STATUS_APPROVED
            costing.quotation_approved_by = self.ceo
            costing.save(update_fields=["quotation_status", "quotation_approved_by", "updated_at"])
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="ceo_approved", assigned_user=self.sales
            ).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            costing.quotation_status = CostingHeader.QUOTATION_STATUS_REJECTED
            costing.quotation_rejected_by = self.ceo
            costing.save(update_fields=["quotation_status", "quotation_rejected_by", "updated_at"])
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="ceo_rejected", assigned_user=self.sales
            ).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            order = ProductionOrder.objects.create(
                title="Event Order",
                order_code="PO-EVENT-001",
                lead=self.lead,
                assigned_production_manager=self.production,
                created_by=self.ceo,
            )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="production_created",
                assigned_user=self.production,
                record_object_id=order.pk,
            ).exists()
        )

        token = set_current_actor(self.ceo)
        try:
            self.sales_other.first_name = "Other"
            self.sales_other.last_name = "Sales"
            self.sales_other.save(update_fields=["first_name", "last_name"])
            with self.captureOnCommitCallbacks(execute=True):
                task = LeadTask.objects.create(
                    lead=self.lead,
                    title="Call buyer",
                    assigned_to="Other Sales",
                )
        finally:
            reset_current_actor(token)
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="task_assigned", assigned_user=self.sales_other
            ).exists()
        )

        token = set_current_actor(self.sales_other)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                task.status = "Done"
                task.save(update_fields=["status"])
        finally:
            reset_current_actor(token)
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="task_completed", assigned_user=self.sales
            ).exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            comment = LeadComment.objects.create(
                lead=self.lead,
                author=self.sales_other.username,
                author_user=self.sales_other,
                content="Buyer confirmed the revised delivery date.",
            )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="comment_added",
                assigned_user=self.sales,
                record_object_id=self.lead.pk,
            ).exists()
        )

    def test_notification_page_query_count_is_bounded(self):
        for index in range(20):
            AutomationNotification.objects.create(
                source_key=f"test:query:{index}",
                title=f"Notification {index}",
                assigned_user=self.ceo,
            )
        client = Client()
        client.force_login(self.ceo)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(reverse("notification_list"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 12)

    def test_bell_caps_count_and_uses_cache_after_first_query(self):
        for index in range(105):
            AutomationNotification.objects.create(
                source_key=f"test:bell:{index}",
                title=f"Bell {index}",
                assigned_user=self.ceo,
            )
        cache.clear()
        request = RequestFactory().get("/main-dashboard/")
        request.user = self.ceo
        request.resolver_match = type("Resolver", (), {"url_name": "main_dashboard"})()
        with CaptureQueriesContext(connection) as first_queries:
            first = operations_header(request)
        with CaptureQueriesContext(connection) as cached_queries:
            second = operations_header(request)
        self.assertEqual(first["crm_header_unread_count"], 105)
        self.assertEqual(second["crm_header_unread_count"], 105)
        self.assertGreater(len(first_queries), 0)
        self.assertEqual(len(cached_queries), 0)

        client = Client()
        client.force_login(self.ceo)
        response = client.get(reverse("notification_list"))
        self.assertContains(response, "99+")

    def test_open_marks_read_redirects_and_rechecks_permission(self):
        item = AutomationNotification.objects.create(
            source_key="test:open:lead",
            title="Open lead",
            rule_type="leads",
            assigned_user=self.sales,
            record_content_type=ContentType.objects.get_for_model(Lead),
            record_object_id=self.lead.pk,
            target_url=reverse("lead_detail", args=[self.lead.pk]),
        )
        client = Client()
        client.force_login(self.sales)
        response = client.get(reverse("notification_open", args=[item.pk]))
        self.assertRedirects(response, reverse("lead_detail", args=[self.lead.pk]), fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertTrue(item.is_read)

        item.assigned_user = self.sales_other
        item.is_read = False
        item.save(update_fields=["assigned_user", "is_read"])
        client.force_login(self.sales_other)
        self.assertEqual(client.get(reverse("notification_open", args=[item.pk])).status_code, 404)

    def test_filter_persists_in_session_and_searches_related_records(self):
        item = AutomationNotification.objects.create(
            source_key="test:search:lead",
            title="Owner comment",
            rule_type="leads",
            notification_type="comment_added",
            priority="information",
            assigned_user=self.sales,
            record_content_type=ContentType.objects.get_for_model(Lead),
            record_object_id=self.lead.pk,
            record_label=self.lead.lead_id,
            target_url=reverse("lead_detail", args=[self.lead.pk]),
        )
        client = Client()
        client.force_login(self.sales)
        response = client.get(
            reverse("notification_list"),
            {"filter": "information", "q": self.customer.account_brand},
        )
        self.assertContains(response, item.title)
        self.assertEqual(response.context["selected_filter"], "information")

        remembered = client.get(reverse("notification_list"))
        self.assertEqual(remembered.context["selected_filter"], "information")


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
        self.assertFalse(
            AutomationNotification.objects.filter(source_key__startswith="operations:").exists()
        )

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
        response = client.get(reverse("leads_list"), {"view": "my"})
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


class LeadOwnershipWorkflowTests(OperationsControlBase):
    def setUp(self):
        super().setUp()
        self.available = Lead.objects.create(
            account_brand="Available Lead",
            lead_status="New",
            lead_type="inbound",
        )
        self.converted = Lead.objects.create(
            account_brand="Converted Unassigned",
            lead_status="Converted",
            lead_type="inbound",
        )
        self.archived = Lead.objects.create(
            account_brand="Archived Unassigned",
            lead_status="New",
            lead_type="inbound",
            is_archived=True,
        )
        self.manager = self.User.objects.create_user("ops-manager", password="test-pass")
        Group.objects.get_or_create(name="Manager")[0].user_set.add(self.manager)
        self.admin = self.User.objects.create_user("ops-admin", password="test-pass")
        Group.objects.get_or_create(name="Admin")[0].user_set.add(self.admin)

    def test_available_is_default_and_my_leads_is_owner_only(self):
        client = Client()
        client.force_login(self.sales)

        available_response = client.get(reverse("leads_list"))
        self.assertContains(available_response, self.available.lead_id)
        self.assertNotContains(available_response, self.lead.lead_id)
        self.assertNotContains(available_response, self.other_lead.lead_id)
        self.assertNotContains(available_response, self.converted.lead_id)
        self.assertNotContains(available_response, self.archived.lead_id)
        self.assertContains(available_response, "New / Unassigned Leads")
        self.assertContains(available_response, "Claim Lead")

        my_response = client.get(reverse("leads_list"), {"view": "my"})
        self.assertContains(my_response, self.lead.lead_id)
        self.assertNotContains(my_response, self.available.lead_id)
        self.assertNotContains(my_response, self.other_lead.lead_id)

    def test_legacy_useraccess_sales_user_is_not_locked_out(self):
        legacy = self.User.objects.create_user("legacy-sales", password="test-pass")
        access, _ = UserAccess.objects.get_or_create(user=legacy)
        access.can_leads = True
        access.save(update_fields=["can_leads"])
        client = Client()
        client.force_login(legacy)

        response = client.get(reverse("leads_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.available.lead_id)
        client.post(reverse("lead_claim", args=[self.available.pk]))
        self.available.refresh_from_db()
        self.assertEqual(self.available.assigned_to, legacy)

    def test_claim_and_release_reuse_one_lead_and_create_audit_history(self):
        client = Client()
        client.force_login(self.sales)
        lead_count = Lead.objects.count()

        with self.captureOnCommitCallbacks(execute=True):
            claim_response = client.post(reverse("lead_claim", args=[self.available.pk]))
        self.available.refresh_from_db()

        self.assertEqual(claim_response.status_code, 302)
        self.assertEqual(self.available.assigned_to, self.sales)
        self.assertEqual(Lead.objects.count(), lead_count)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                actor=self.sales,
                module="leads",
                record_id=str(self.available.pk),
                field_name="assigned_to",
                new_value=str(self.sales.pk),
            ).exists()
        )
        available_ids = [lead.pk for lead in client.get(reverse("leads_list")).context["page_obj"]]
        my_ids = [
            lead.pk
            for lead in client.get(reverse("leads_list"), {"view": "my"}).context["page_obj"]
        ]
        self.assertNotIn(self.available.pk, available_ids)
        self.assertIn(self.available.pk, my_ids)

        with self.captureOnCommitCallbacks(execute=True):
            release_response = client.post(reverse("lead_release", args=[self.available.pk]))
        self.available.refresh_from_db()

        self.assertEqual(release_response.status_code, 302)
        self.assertIsNone(self.available.assigned_to)
        self.assertEqual(Lead.objects.count(), lead_count)
        available_ids = [lead.pk for lead in client.get(reverse("leads_list")).context["page_obj"]]
        self.assertIn(self.available.pk, available_ids)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                actor=self.sales,
                module="leads",
                record_id=str(self.available.pk),
                field_name="assigned_to",
                previous_value=str(self.sales.pk),
                new_value="",
            ).exists()
        )

    def test_claim_rejects_closed_archived_and_already_assigned_leads(self):
        client = Client()
        client.force_login(self.sales)

        for lead in (self.converted, self.archived, self.other_lead):
            original_owner = lead.assigned_to_id
            client.post(reverse("lead_claim", args=[lead.pk]))
            lead.refresh_from_db()
            self.assertEqual(lead.assigned_to_id, original_owner)

    def test_sales_cannot_view_or_mutate_another_salespersons_records(self):
        client = Client()
        client.force_login(self.sales)

        self.assertEqual(client.get(reverse("lead_detail", args=[self.other_lead.pk])).status_code, 404)
        self.assertEqual(client.get(reverse("lead_edit", args=[self.other_lead.pk])).status_code, 404)
        response = client.post(
            reverse("lead_bulk_update"),
            {
                "lead_ids": [self.other_lead.pk],
                "bulk_action": "followup",
                "next_follow_up_date": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.other_lead.refresh_from_db()
        self.assertIsNone(self.other_lead.next_follow_up_date)

    def test_manager_and_ceo_can_view_all_and_release_any_lead(self):
        for user in (self.manager, self.ceo, self.admin):
            client = Client()
            client.force_login(user)
            response = client.get(reverse("leads_list"), {"view": "all"})
            self.assertContains(response, self.lead.lead_id)
            self.assertContains(response, self.other_lead.lead_id)
            self.assertContains(response, self.available.lead_id)

        client = Client()
        client.force_login(self.manager)
        client.post(reverse("lead_release", args=[self.other_lead.pk]))
        self.other_lead.refresh_from_db()
        self.assertIsNone(self.other_lead.assigned_to)

    def test_conversion_preserves_linked_lead_owner_and_opportunity_scope(self):
        client = Client()
        client.force_login(self.sales)
        client.post(reverse("lead_claim", args=[self.available.pk]))
        lead_count = Lead.objects.count()

        response = client.post(reverse("convert_lead_to_opportunity", args=[self.available.pk]))
        opportunity = Opportunity.objects.get(lead=self.available)
        self.available.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.available.assigned_to, self.sales)
        self.assertEqual(self.available.lead_status, "Converted")
        self.assertEqual(opportunity.lead.assigned_to, self.sales)
        self.assertEqual(Lead.objects.count(), lead_count)
        self.assertContains(client.get(reverse("opportunities_list")), opportunity.opportunity_id)

        client.force_login(self.sales_other)
        self.assertNotContains(client.get(reverse("opportunities_list")), opportunity.opportunity_id)
        self.assertEqual(client.get(reverse("opportunity_detail", args=[opportunity.pk])).status_code, 404)

        client.force_login(self.ceo)
        self.assertContains(client.get(reverse("opportunities_list")), opportunity.opportunity_id)

    def test_production_users_see_only_assigned_orders_while_ceo_sees_all(self):
        other_production = self.User.objects.create_user("ops-production-other", password="test-pass")
        Group.objects.get(name="Production").user_set.add(other_production)
        own_order = ProductionOrder.objects.create(
            title="Own Production",
            order_code="PO-OWNERSHIP-OWN",
            assigned_production_manager=self.production,
        )
        other_order = ProductionOrder.objects.create(
            title="Other Production",
            order_code="PO-OWNERSHIP-OTHER",
            assigned_production_manager=other_production,
        )

        client = Client()
        client.force_login(self.production)
        response = client.get(reverse("production_list"), {"status": "all"})
        self.assertContains(response, own_order.order_code)
        self.assertNotContains(response, other_order.order_code)

        for user in (self.ceo, self.admin):
            client.force_login(user)
            response = client.get(reverse("production_list"), {"status": "all"})
            self.assertContains(response, own_order.order_code)
            self.assertContains(response, other_order.order_code)

    def test_lead_queue_query_count_is_bounded(self):
        for index in range(25):
            Lead.objects.create(
                account_brand=f"Available Performance {index}",
                lead_status="New",
                lead_type="inbound",
            )
        client = Client()
        client.force_login(self.sales)
        client.get(reverse("leads_list"), {"view": "available", "per_page": 20})

        with CaptureQueriesContext(connection) as twenty_queries:
            response = client.get(reverse("leads_list"), {"view": "available", "per_page": 20})
        with CaptureQueriesContext(connection) as fifty_queries:
            larger_response = client.get(reverse("leads_list"), {"view": "available", "per_page": 50})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(larger_response.status_code, 200)
        self.assertLessEqual(len(twenty_queries), 20)
        self.assertLessEqual(abs(len(fifty_queries) - len(twenty_queries)), 2)
