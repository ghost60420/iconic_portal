from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CostingHeader, CostingLineItem, Customer, Lead, Opportunity, OrderLifecycle
from crm.services.order_lifecycle import build_lifecycle_profit_breakdown
from crm.services.costing_workflow import (
    build_production_profit_snapshot,
    convert_costing_to_quotation,
    create_invoice_from_costing,
    create_or_link_production_order_from_invoice,
)


class CostingInvoiceWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="workflow-admin",
            email="workflow-admin@example.com",
            password="test-pass",
        )
        self.customer = Customer.objects.create(
            account_brand="Workflow Brand",
            contact_name="Workflow Buyer",
            email="buyer@example.com",
            country="Canada",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Workflow Brand",
            contact_name="Workflow Buyer",
            email="buyer@example.com",
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            stage="Proposal",
            product_type="Activewear",
            product_category="Leggings",
            moq_units=10,
        )
        self.costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            style_name="Workflow Legging",
            product_type="Activewear",
            factory_location="bd",
            order_quantity=10,
            currency="CAD",
            manual_fob_per_piece=Decimal("25.00"),
            status="approved",
        )
        CostingLineItem.objects.create(
            costing=self.costing,
            category="fabric",
            item_name="Main fabric",
            uom="piece",
            unit_price=Decimal("10.00"),
            consumption_value=Decimal("1.00"),
        )
        CostingLineItem.objects.create(
            costing=self.costing,
            category="cm_labor",
            item_name="Sewing labor",
            uom="piece",
            unit_price=Decimal("4.00"),
            consumption_value=Decimal("1.00"),
        )

    def test_costing_to_quote_to_invoice_to_production_tracks_profit(self):
        convert_costing_to_quotation(self.costing, user=self.user)
        self.costing.refresh_from_db()
        self.costing.quotation_status = CostingHeader.QUOTATION_STATUS_APPROVED
        self.costing.quotation_approved_by = self.user
        self.costing.quotation_approved_at = self.costing.quoted_at
        self.costing.save(
            update_fields=[
                "quotation_status",
                "quotation_approved_by",
                "quotation_approved_at",
                "updated_at",
            ]
        )

        self.assertTrue(self.costing.quotation_number.startswith("QT"))
        self.assertIsNotNone(self.costing.quoted_at)
        lifecycle = OrderLifecycle.objects.get(quotation=self.costing)
        self.assertEqual(lifecycle.status, "quotation")

        invoice, created_invoice = create_invoice_from_costing(self.costing, user=self.user)
        self.assertTrue(created_invoice)
        self.assertEqual(OrderLifecycle.objects.count(), 1)
        self.assertEqual(invoice.costing_header, self.costing)
        self.assertEqual(invoice.total_amount, Decimal("250.00"))
        self.assertEqual(invoice.sewing_charge, Decimal("40.00"))
        self.assertEqual(invoice.other_internal_cost, Decimal("100.00"))
        self.assertEqual(invoice.estimated_gross_profit, Decimal("110.00"))
        invoice.paid_amount = Decimal("75.00")
        invoice.status = "partial"
        invoice.deposit_percentage = Decimal("30.00")
        invoice.save(update_fields=["paid_amount", "status", "deposit_percentage", "updated_at"])

        order, created_order = create_or_link_production_order_from_invoice(invoice, user=self.user)
        invoice.refresh_from_db()
        self.opportunity.refresh_from_db()

        self.assertTrue(created_order)
        self.assertEqual(OrderLifecycle.objects.count(), 1)
        self.assertEqual(order.costing_header, self.costing)
        self.assertEqual(invoice.order, order)
        self.assertEqual(self.opportunity.stage, "Production")

        snapshot = build_production_profit_snapshot(order)
        self.assertEqual(snapshot["invoice_total"], Decimal("250.00"))
        self.assertEqual(snapshot["standard_cost"], Decimal("140.0000"))
        self.assertEqual(snapshot["estimated_profit"], Decimal("110.0000"))

        lifecycle.refresh_from_db()
        self.assertEqual(lifecycle.status, "production")
        breakdown = build_lifecycle_profit_breakdown(lifecycle)
        self.assertEqual(breakdown["invoice_total"], Decimal("250.00"))
        self.assertEqual(breakdown["sewing_cost"], Decimal("40.00"))
        self.assertEqual(breakdown["fabric_cost"], Decimal("100.0000"))
        self.assertEqual(breakdown["trim_cost"], Decimal("0.0000"))
        self.assertEqual(breakdown["net_profit"], Decimal("110.0000"))
