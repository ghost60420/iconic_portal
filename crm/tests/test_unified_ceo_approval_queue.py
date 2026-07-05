from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AutomationNotification,
    CostingHeader,
    CostingLineItem,
    CRMAuditLog,
    Invoice,
    Lead,
    Opportunity,
    QuickCosting,
)


class UnifiedCEOApprovalQueueTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.sales = user_model.objects.create_user(
            username="queue-sales",
            password="test-pass",
            first_name="Sales",
            last_name="Person",
        )
        self.ceo = user_model.objects.create_user(
            username="queue-ceo",
            password="test-pass",
            first_name="CEO",
            last_name="User",
        )
        self.accounts = user_model.objects.create_user(
            username="queue-accounts",
            password="test-pass",
            first_name="Accounts",
            last_name="Manager",
        )
        self.regular = user_model.objects.create_user(
            username="queue-regular",
            password="test-pass",
        )
        self.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        self.accounts.groups.add(Group.objects.get_or_create(name="Accounts")[0])
        for user, approve in ((self.sales, False), (self.ceo, True)):
            access = user.access
            access.can_costing = True
            access.can_view_internal_costing = True
            access.can_costing_approve = approve
            access.save()

        self.lead = Lead.objects.create(
            account_brand="Unified Queue Brand",
            contact_name="Taylor Buyer",
            email="buyer@example.com",
            product_category="Hoodie",
            primary_product_type="Streetwear",
            order_quantity="100",
            assigned_to=self.sales,
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            product_category="Hoodie",
            product_type="Streetwear",
            moq_units=100,
            order_value=Decimal("1500.00"),
        )

    def _quick(self, **overrides):
        values = {
            "opportunity": self.opportunity,
            "account_brand": "Unified Queue Brand",
            "contact_name": "Taylor Buyer",
            "buyer_name": "Unified Queue Brand",
            "project_name": "Quick Hoodie",
            "product_type": "Streetwear",
            "quantity": 100,
            "currency": "CAD",
            "material_cost": Decimal("500.00"),
            "production_cost": Decimal("300.00"),
            "other_expenses": Decimal("200.00"),
            "shipping_cost": Decimal("100.00"),
            "selling_price_per_piece": Decimal("15.00"),
            "commission_percent": Decimal("5.00"),
            "created_by": self.sales,
        }
        values.update(overrides)
        return QuickCosting.objects.create(**values)

    def _advanced(self, number="QT20269901", **overrides):
        values = {
            "opportunity": self.opportunity,
            "style_name": "Advanced Hoodie",
            "product_type": "Streetwear",
            "factory_location": "bd",
            "order_quantity": 100,
            "currency": "CAD",
            "manual_fob_per_piece": Decimal("15.00"),
            "status": "approved",
            "quotation_number": number,
            "quotation_status": CostingHeader.QUOTATION_STATUS_DRAFT,
            "quoted_by": self.sales,
            "quoted_at": timezone.now(),
        }
        values.update(overrides)
        costing = CostingHeader.objects.create(**values)
        CostingLineItem.objects.create(
            costing=costing,
            category="fabric",
            item_name="Main fabric",
            uom="piece",
            unit_price=Decimal("5.00"),
            consumption_value=Decimal("1.00"),
        )
        return costing

    def _submit_quick(self, quick):
        self.client.force_login(self.sales)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("quick_costing_submit_for_approval", args=[quick.pk]))
        quick.refresh_from_db()
        return response

    def test_queue_combines_only_submitted_pending_quick_and_advanced_costings(self):
        draft = self._quick(project_name="Unsubmitted Draft")
        submitted = self._quick(project_name="Submitted Quick")
        advanced = self._advanced()
        self._submit_quick(submitted)
        self.client.force_login(self.ceo)

        response = self.client.get(reverse("ceo_quotation_approval_queue"))
        rows = response.context["rows"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["costing_type"] for row in rows}, {"Quick", "Advanced"})
        quick_row = next(row for row in rows if row["costing_type"] == "Quick")
        self.assertEqual(quick_row["record"].pk, submitted.pk)
        self.assertEqual(quick_row["currency"], "CAD")
        self.assertEqual(quick_row["total_amount"], Decimal("1500.00"))
        self.assertEqual(quick_row["profit_amount"], Decimal("325.000"))
        self.assertEqual(quick_row["profit_margin"], Decimal("21.66666666666666666666666667"))
        self.assertContains(response, advanced.quotation_number)
        self.assertContains(response, self.lead.lead_id)
        self.assertContains(response, self.opportunity.opportunity_id)
        self.assertContains(response, "Unified Queue Brand")
        self.assertContains(response, "Sales Person")
        self.assertContains(response, "CAD $1,500.00")
        self.assertContains(response, "Submitted for CEO Approval")
        self.assertFalse(any(row["record"].pk == draft.pk and row["costing_type"] == "Quick" for row in rows))

    def test_sales_user_with_accounts_role_can_submit_quick_costing(self):
        self.sales.groups.add(Group.objects.get(name="Accounts"))
        quick = self._quick(project_name="Multi-role Sales Quick")
        self.client.force_login(self.sales)

        detail_response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            submit_response = self.client.post(
                reverse("quick_costing_submit_for_approval", args=[quick.pk])
            )
        quick.refresh_from_db()

        self.assertContains(detail_response, "Submit for CEO Approval")
        self.assertEqual(submit_response.status_code, 302)
        self.assertEqual(quick.status, QuickCosting.STATUS_DRAFT)
        self.assertEqual(quick.approval_submitted_by, self.sales)
        self.assertIsNotNone(quick.approval_submitted_at)
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="ceo_approval",
                assigned_user=self.ceo,
                record_object_id=quick.pk,
            ).exists()
        )
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="quick_costing",
                record_id=str(quick.pk),
                field_name="approval_submitted_at",
                actor=self.sales,
            ).exists()
        )

        self.client.force_login(self.ceo)
        queue_response = self.client.get(reverse("ceo_quotation_approval_queue"))
        self.assertTrue(
            any(
                row["record"].pk == quick.pk and row["costing_type"] == "Quick"
                for row in queue_response.context["rows"]
            )
        )

    def test_advanced_queue_uses_assigned_salesperson_not_conversion_actor(self):
        advanced = self._advanced(quoted_by=self.ceo)
        self.client.force_login(self.ceo)

        response = self.client.get(reverse("ceo_quotation_approval_queue"))
        row = next(
            row for row in response.context["rows"]
            if row["costing_type"] == "Advanced" and row["record"].pk == advanced.pk
        )

        self.assertEqual(row["salesperson"], "Sales Person")

    def test_quick_submit_approve_and_reject_use_existing_fields_and_notifications(self):
        approved = self._quick(project_name="Approve Me")
        rejected = self._quick(project_name="Reject Me")
        self._submit_quick(approved)
        self._submit_quick(rejected)
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="ceo_approval",
                assigned_user=self.ceo,
                record_object_id=approved.pk,
            ).exists()
        )
        self.client.force_login(self.ceo)

        with self.captureOnCommitCallbacks(execute=True):
            approve_response = self.client.post(reverse("quick_costing_approve", args=[approved.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            reject_response = self.client.post(reverse("quick_costing_reject", args=[rejected.pk]))
        approved.refresh_from_db()
        rejected.refresh_from_db()

        self.assertEqual(approve_response.status_code, 302)
        self.assertEqual(approved.status, QuickCosting.STATUS_APPROVED)
        self.assertEqual(approved.approved_by, self.ceo)
        self.assertIsNotNone(approved.approved_at)
        self.assertEqual(reject_response.status_code, 302)
        self.assertEqual(rejected.status, QuickCosting.STATUS_REJECTED)
        self.assertEqual(rejected.rejected_by, self.ceo)
        self.assertIsNotNone(rejected.rejected_at)
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="ceo_approved",
                assigned_user=self.accounts,
                record_object_id=approved.pk,
            ).exists()
        )
        self.assertTrue(
            AutomationNotification.objects.filter(
                notification_type="ceo_rejected",
                assigned_user=self.sales,
                record_object_id=rejected.pk,
            ).exists()
        )
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="quick_costing",
                record_id=str(approved.pk),
                action_type=CRMAuditLog.ACTION_APPROVED,
                actor=self.ceo,
            ).exists()
        )

    def test_rejected_quick_costing_can_be_edited_and_resubmitted(self):
        quick = self._quick()
        self._submit_quick(quick)
        first_submission = quick.approval_submitted_at
        self.client.force_login(self.ceo)
        self.client.post(reverse("quick_costing_reject", args=[quick.pk]))
        self.client.force_login(self.sales)

        edit_response = self.client.get(reverse("quick_costing_edit", args=[quick.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            submit_response = self.client.post(reverse("quick_costing_submit_for_approval", args=[quick.pk]))
        quick.refresh_from_db()

        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(submit_response.status_code, 302)
        self.assertEqual(quick.status, QuickCosting.STATUS_DRAFT)
        self.assertGreaterEqual(quick.approval_submitted_at, first_submission)
        self.assertIsNone(quick.rejected_by)
        self.client.force_login(self.ceo)
        queue_response = self.client.get(reverse("ceo_quotation_approval_queue"))
        self.assertTrue(
            any(
                row["record"].pk == quick.pk and row["costing_type"] == "Quick"
                for row in queue_response.context["rows"]
            )
        )

    def test_advanced_approve_and_reject_behavior_is_preserved(self):
        approved = self._advanced("QT20269902")
        rejected = self._advanced("QT20269903")
        self.client.force_login(self.ceo)

        approve_response = self.client.post(reverse("cost_sheet_quotation_approve", args=[approved.pk]))
        reject_response = self.client.post(reverse("cost_sheet_quotation_reject", args=[rejected.pk]))
        approved.refresh_from_db()
        rejected.refresh_from_db()

        self.assertEqual(approve_response.status_code, 302)
        self.assertEqual(approved.quotation_status, CostingHeader.QUOTATION_STATUS_APPROVED)
        self.assertEqual(approved.quotation_approved_by, self.ceo)
        self.assertIsNotNone(approved.quotation_approved_at)
        self.assertEqual(reject_response.status_code, 302)
        self.assertEqual(rejected.quotation_status, CostingHeader.QUOTATION_STATUS_REJECTED)
        self.assertEqual(rejected.quotation_rejected_by, self.ceo)

    def test_assigned_salesperson_can_edit_and_resubmit_rejected_advanced_costing(self):
        costing = self._advanced("QT20269905")
        costing.quotation_status = CostingHeader.QUOTATION_STATUS_REJECTED
        costing.quotation_rejected_by = self.ceo
        costing.quotation_rejected_at = timezone.now()
        costing.save(
            update_fields=["quotation_status", "quotation_rejected_by", "quotation_rejected_at", "updated_at"]
        )
        original_number = costing.quotation_number
        self.client.force_login(self.sales)

        detail_response = self.client.get(reverse("cost_sheet_detail", args=[costing.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            resubmit_response = self.client.post(reverse("cost_sheet_quotation_resubmit", args=[costing.pk]))
        costing.refresh_from_db()

        self.assertEqual(detail_response.status_code, 200)
        self.assertFalse(detail_response.context["is_locked"])
        self.assertContains(detail_response, "Resubmit for CEO Approval")
        self.assertEqual(resubmit_response.status_code, 302)
        self.assertEqual(costing.quotation_status, CostingHeader.QUOTATION_STATUS_DRAFT)
        self.assertEqual(costing.quotation_number, original_number)
        self.assertEqual(costing.quoted_by, self.sales)
        self.assertIsNone(costing.quotation_rejected_by)
        self.assertTrue(
            costing.audits.filter(action="quoted", changed_by=self.sales, note="Resubmitted for CEO approval").exists()
        )

    def test_accounting_queue_contains_only_approved_items_and_supports_existing_conversion(self):
        approved = self._quick(status=QuickCosting.STATUS_APPROVED, approved_by=self.ceo, approved_at=timezone.now())
        rejected = self._quick(project_name="Rejected Quick", status=QuickCosting.STATUS_REJECTED, rejected_by=self.ceo)
        draft = self._quick(project_name="Draft Quick")
        approved.approval_submitted_by = self.sales
        approved.approval_submitted_at = timezone.now()
        approved.save(update_fields=["approval_submitted_by", "approval_submitted_at", "updated_at"])
        rejected.approval_submitted_by = self.sales
        rejected.approval_submitted_at = timezone.now()
        rejected.save(update_fields=["approval_submitted_by", "approval_submitted_at", "updated_at"])
        self.client.force_login(self.accounts)

        queue_response = self.client.get(reverse("ceo_quotation_approval_queue"), {"status": "pending"})
        queue_quick_ids = {
            row["record"].pk
            for row in queue_response.context["rows"]
            if row["costing_type"] == "Quick"
        }
        quotation_response = self.client.post(reverse("quick_costing_convert_to_quotation", args=[approved.pk]))
        approved.refresh_from_db()
        invoice_response = self.client.post(reverse("quick_costing_convert_to_invoice", args=[approved.pk]))
        approved.refresh_from_db()

        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(queue_quick_ids, {approved.pk})
        self.assertEqual(quotation_response.status_code, 302)
        self.assertEqual(approved.status, QuickCosting.STATUS_INVOICED)
        self.assertEqual(invoice_response.status_code, 302)
        self.assertTrue(Invoice.objects.filter(quick_costing=approved).exists())
        self.client.force_login(self.ceo)
        pending_response = self.client.get(reverse("ceo_quotation_approval_queue"))
        self.assertNotContains(pending_response, approved.project_name)
        self.assertEqual(draft.status, QuickCosting.STATUS_DRAFT)

    def test_queue_has_no_duplicate_rows(self):
        quick = self._quick()
        self._submit_quick(quick)
        self.client.force_login(self.ceo)

        response = self.client.get(reverse("ceo_quotation_approval_queue"))
        matching_rows = [
            row for row in response.context["rows"]
            if row["costing_type"] == "Quick" and row["record"].pk == quick.pk
        ]

        self.assertEqual(len(matching_rows), 1)

    def test_advanced_rows_do_not_add_queue_queries(self):
        self._advanced("QT20269910")
        self.client.force_login(self.ceo)
        with CaptureQueriesContext(connection) as one_row_queries:
            response = self.client.get(reverse("ceo_quotation_approval_queue"))
        self.assertEqual(response.status_code, 200)

        for number in range(11, 16):
            self._advanced(f"QT202699{number}")
        with CaptureQueriesContext(connection) as six_row_queries:
            response = self.client.get(reverse("ceo_quotation_approval_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(six_row_queries), len(one_row_queries))

    def test_quick_rows_do_not_add_queue_queries(self):
        first = self._quick(project_name="First submitted quick")
        self._submit_quick(first)
        self.client.force_login(self.ceo)
        with CaptureQueriesContext(connection) as one_row_queries:
            response = self.client.get(reverse("ceo_quotation_approval_queue"))
        self.assertEqual(response.status_code, 200)

        for number in range(2, 7):
            quick = self._quick(project_name=f"Submitted quick {number}")
            self._submit_quick(quick)
        self.client.force_login(self.ceo)
        with CaptureQueriesContext(connection) as six_row_queries:
            response = self.client.get(reverse("ceo_quotation_approval_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(six_row_queries), len(one_row_queries))

    def test_only_ceo_or_authorized_approver_can_decide(self):
        quick = self._quick()
        advanced = self._advanced("QT20269904")
        self._submit_quick(quick)

        for user in (self.sales, self.accounts, self.regular):
            self.client.force_login(user)
            response = self.client.post(reverse("quick_costing_approve", args=[quick.pk]))
            advanced_response = self.client.post(reverse("cost_sheet_quotation_approve", args=[advanced.pk]))
            quick.refresh_from_db()
            advanced.refresh_from_db()
            self.assertNotEqual(quick.status, QuickCosting.STATUS_APPROVED)
            self.assertNotEqual(advanced.quotation_status, CostingHeader.QUOTATION_STATUS_APPROVED)
            self.assertIn(response.status_code, {302, 403})
            self.assertIn(advanced_response.status_code, {302, 403})

        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("ceo_quotation_approval_queue")).status_code, 403)
