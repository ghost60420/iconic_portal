from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    CostingHeader,
    CostingLineItem,
    Lead,
    Opportunity,
    OpportunityTask,
    ProductionOrder,
)
from crm.services.production_operational_status import sync_operational_status


class WorkflowSafetyUpdateTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="workflow-admin",
            email="workflow-admin@example.com",
            password="test-pass",
        )
        self.staff = user_model.objects.create_user(
            username="factory-user",
            email="factory-user@example.com",
            password="test-pass",
            first_name="Factory",
            last_name="User",
            is_staff=True,
        )
        self.regular = user_model.objects.create_user(
            username="sales-user",
            email="sales-user@example.com",
            password="test-pass",
        )
        self.lead = Lead.objects.create(
            account_brand="Safety Brand",
            contact_name="Sam Buyer",
            email="sam@example.com",
            order_quantity="100",
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            stage="Proposal",
            product_type="Activewear",
            product_category="Hoodie",
            moq_units=100,
            order_value=Decimal("1000.00"),
        )

    def test_opportunity_task_assigned_to_dropdown_and_save(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<select name="task_assigned_to">', html=False)
        self.assertContains(response, "Factory User")

        response = self.client.post(
            reverse("opportunity_detail", args=[self.opportunity.pk]),
            {
                "action": "add_opp_task",
                "task_title": "Follow up sample approval",
                "task_assigned_to": "Factory User",
                "task_priority": "High",
            },
        )
        self.assertEqual(response.status_code, 302)
        task = OpportunityTask.objects.get(opportunity=self.opportunity)
        self.assertEqual(task.assigned_to, "Factory User")

    def test_lead_archive_hides_from_active_list_and_keeps_detail(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("lead_archive", args=[self.lead.pk]))
        self.assertEqual(response.status_code, 302)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.is_archived)
        self.assertEqual(self.lead.archived_by, self.admin)

        active_response = self.client.get(reverse("leads_list"))
        self.assertNotContains(active_response, self.lead.lead_id)
        archived_response = self.client.get(reverse("leads_list"), {"archive": "archived"})
        self.assertContains(archived_response, self.lead.lead_id)
        detail_response = self.client.get(reverse("lead_detail", args=[self.lead.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Archive Status")
        self.assertContains(detail_response, "Archived by:")
        self.assertContains(detail_response, "workflow-admin")

    def test_opportunity_archive_replaces_hard_delete(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("opportunity_delete", args=[self.opportunity.pk]))
        self.assertEqual(response.status_code, 302)
        self.opportunity.refresh_from_db()
        self.assertTrue(self.opportunity.is_archived)
        self.assertEqual(self.opportunity.archived_by, self.admin)

        active_response = self.client.get(reverse("opportunities_list"))
        self.assertNotContains(active_response, reverse("opportunity_detail", args=[self.opportunity.pk]))
        archived_response = self.client.get(reverse("opportunities_list"), {"archive": "archived", "status": "all"})
        self.assertContains(archived_response, reverse("opportunity_detail", args=[self.opportunity.pk]))
        detail_response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertContains(detail_response, "Archive Status")
        self.assertContains(detail_response, "Archived by:")
        self.assertContains(detail_response, "workflow-admin")

    def test_production_archive_requires_confirmation_for_ready_to_ship(self):
        self.client.force_login(self.admin)
        order = ProductionOrder.objects.create(
            lead=self.lead,
            opportunity=self.opportunity,
            title="Safety Production",
            qty_total=100,
        )
        sync_operational_status(order, explicit_status="ready_to_ship")
        order.refresh_from_db()

        response = self.client.post(reverse("production_archive", args=[order.pk]))
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertFalse(order.is_archived)

        response = self.client.post(
            reverse("production_archive", args=[order.pk]),
            {"confirm_archive": "archive"},
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertTrue(order.is_archived)
        self.assertEqual(order.archived_by, self.admin)

        active_response = self.client.get(reverse("production_list"))
        self.assertNotContains(active_response, reverse("production_detail", args=[order.pk]))
        archived_response = self.client.get(reverse("production_list"), {"archive": "archived", "status": "all"})
        self.assertContains(archived_response, reverse("production_detail", args=[order.pk]))
        detail_response = self.client.get(reverse("production_detail", args=[order.pk]))
        self.assertContains(detail_response, "Archive Status")
        self.assertContains(detail_response, "workflow-admin")

    def test_non_staff_user_cannot_archive_workflow_records(self):
        self.client.force_login(self.regular)

        response = self.client.post(reverse("lead_archive", args=[self.lead.pk]))
        self.assertEqual(response.status_code, 302)
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_archived)

    def _quotation_costing(self):
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            style_name="Safety Hoodie",
            product_type="Activewear",
            factory_location="bd",
            order_quantity=100,
            currency="CAD",
            manual_fob_per_piece=Decimal("12.00"),
            status="approved",
            quotation_number="QT20260001",
            quoted_by=self.admin,
            quoted_at=timezone.now(),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="fabric",
            item_name="Main fabric",
            uom="piece",
            unit_price=Decimal("5.00"),
            consumption_value=Decimal("1.00"),
        )
        return costing

    def test_quotation_approval_rejection_and_invoice_guard(self):
        self.client.force_login(self.admin)
        costing = self._quotation_costing()

        response = self.client.get(reverse("cost_sheet_client_quotation", args=[costing.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft")
        self.assertContains(response, "Approved By")
        self.assertContains(response, "Approved Date")
        self.assertContains(response, "Rejected By")
        self.assertContains(response, "Rejected Date")
        self.assertContains(response, "Invoice locked until approved")

        with patch("crm.views_costing.create_invoice_from_costing") as create_invoice:
            response = self.client.post(reverse("cost_sheet_convert_to_invoice", args=[costing.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(create_invoice.called)
        self.assertContains(response, "Approve the quotation before creating an invoice.")

        response = self.client.post(reverse("cost_sheet_quotation_reject", args=[costing.pk]))
        self.assertEqual(response.status_code, 302)
        costing.refresh_from_db()
        self.assertEqual(costing.quotation_status, CostingHeader.QUOTATION_STATUS_REJECTED)
        self.assertEqual(costing.quotation_rejected_by, self.admin)
        self.assertIsNotNone(costing.quotation_rejected_at)

        response = self.client.post(reverse("cost_sheet_quotation_approve", args=[costing.pk]))
        self.assertEqual(response.status_code, 302)
        costing.refresh_from_db()
        self.assertEqual(costing.quotation_status, CostingHeader.QUOTATION_STATUS_APPROVED)
        self.assertEqual(costing.quotation_approved_by, self.admin)
        self.assertIsNotNone(costing.quotation_approved_at)
        self.assertIsNone(costing.quotation_rejected_by)
        self.assertIsNone(costing.quotation_rejected_at)

        fake_invoice = SimpleNamespace(pk=123, invoice_number="INV-TEST")
        with patch(
            "crm.views_costing.create_invoice_from_costing",
            return_value=(fake_invoice, True),
        ) as create_invoice:
            response = self.client.post(reverse("cost_sheet_convert_to_invoice", args=[costing.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(create_invoice.called)

    def test_ceo_quotation_queue_reuses_existing_advanced_quotation_status(self):
        self.client.force_login(self.admin)
        costing = self._quotation_costing()

        response = self.client.get(reverse("ceo_quotation_approval_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, costing.quotation_number)
        self.assertContains(response, self.lead.lead_id)
        self.assertContains(response, self.opportunity.opportunity_id)
        self.assertContains(response, "Submitted for CEO Approval")
        self.assertContains(response, "CAD $1,200.00")
        self.assertContains(response, reverse("cost_sheet_quotation_approve", args=[costing.pk]))
        self.assertContains(response, reverse("cost_sheet_quotation_reject", args=[costing.pk]))
