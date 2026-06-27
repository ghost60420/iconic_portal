from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import CostingHeader, CostingLineItem, Customer, Invoice, Lead, Opportunity, ProductionOrder
from crm.production_forms import ProductionOrderForm
from crm.services.costing_workflow import create_invoice_from_costing, create_or_link_production_order_from_invoice


class ProductionOrderFromQuotationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.ceo = user_model.objects.create_superuser(
            username="production-release-ceo",
            email="ceo@example.com",
            password="test-pass",
        )
        self.manager = user_model.objects.create_user(
            username="production-manager",
            email="manager@example.com",
            password="test-pass",
            first_name="Production",
            last_name="Manager",
        )
        self.customer = Customer.objects.create(
            account_brand="Production Client",
            contact_name="Client Buyer",
            email="buyer@example.com",
            country="Canada",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Production Client",
            contact_name="Client Buyer",
            email="buyer@example.com",
            assigned_to=self.manager,
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            stage="Proposal",
            product_type="Activewear",
            product_category="Hoodie",
            moq_units=100,
        )
        self.costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            style_name="Approved Hoodie",
            product_type="Activewear",
            factory_location="bd",
            order_quantity=100,
            currency="CAD",
            manual_fob_per_piece=Decimal("25.00"),
            status="approved",
            quotation_number="QT20260077",
            quoted_by=self.ceo,
            quoted_at=timezone.now(),
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
        self.client.force_login(self.ceo)

    def _approve(self):
        return self.client.post(reverse("cost_sheet_quotation_approve", args=[self.costing.pk]))

    def test_ceo_approval_creates_production_order_with_locked_snapshot(self):
        response = self._approve()

        self.assertEqual(response.status_code, 302)
        self.costing.refresh_from_db()
        order = ProductionOrder.objects.get(source_quotation=self.costing)
        self.assertEqual(self.costing.quotation_status, CostingHeader.QUOTATION_STATUS_APPROVED)
        self.assertEqual(order.costing_header, self.costing)
        self.assertEqual(order.quotation_number_snapshot, "QT20260077")
        self.assertEqual(order.lead, self.lead)
        self.assertEqual(order.opportunity, self.opportunity)
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.client_name_snapshot, "Production Client")
        self.assertEqual(order.product_type_snapshot, "Activewear")
        self.assertEqual(order.qty_total, 100)
        self.assertEqual(order.approved_currency, "CAD")
        self.assertEqual(order.approved_selling_price, Decimal("25.0000"))
        self.assertEqual(order.approved_total_value, Decimal("2500.0000"))
        self.assertEqual(order.approved_costing_summary["total_cost_per_piece"], "14.0000")
        self.assertEqual(order.approved_costing_summary["total_cost_order"], "1400.0000")
        self.assertEqual(order.operational_status, "planning")
        self.assertEqual(order.assigned_production_manager, self.manager)
        self.assertEqual(order.created_by, self.ceo)
        self.assertIsNotNone(order.approved_price_locked_at)
        self.assertFalse(Invoice.objects.filter(costing_header=self.costing).exists())

    def test_repeated_approval_does_not_create_duplicate_order(self):
        self._approve()
        first_order = ProductionOrder.objects.get(source_quotation=self.costing)

        self._approve()

        self.assertEqual(ProductionOrder.objects.filter(source_quotation=self.costing).count(), 1)
        self.assertEqual(ProductionOrder.objects.get(source_quotation=self.costing), first_order)

    def test_invoice_conversion_reuses_auto_created_production_order(self):
        self._approve()
        approved_order = ProductionOrder.objects.get(source_quotation=self.costing)

        invoice, invoice_created = create_invoice_from_costing(self.costing, user=self.ceo)
        linked_order, order_created = create_or_link_production_order_from_invoice(invoice, user=self.ceo)

        self.assertTrue(invoice_created)
        self.assertFalse(order_created)
        self.assertEqual(linked_order, approved_order)
        self.assertEqual(ProductionOrder.objects.filter(costing_header=self.costing).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.order, approved_order)

    def test_existing_legacy_order_is_linked_instead_of_duplicated(self):
        legacy_order = ProductionOrder.objects.create(
            costing_header=self.costing,
            opportunity=self.opportunity,
            lead=self.lead,
            title="Existing production order",
            qty_total=100,
        )

        self._approve()

        legacy_order.refresh_from_db()
        self.assertEqual(ProductionOrder.objects.filter(costing_header=self.costing).count(), 1)
        self.assertEqual(legacy_order.source_quotation, self.costing)
        self.assertEqual(legacy_order.approved_selling_price, Decimal("25.0000"))

    def test_approved_snapshot_cannot_be_changed_after_creation(self):
        self._approve()
        order = ProductionOrder.objects.get(source_quotation=self.costing)

        order.approved_selling_price = Decimal("99.00")
        with self.assertRaisesMessage(ValidationError, "Approved quotation pricing"):
            order.save()

        order.refresh_from_db()
        self.assertEqual(order.approved_selling_price, Decimal("25.0000"))
        order.notes = "Production-only note"
        order.save(update_fields=["notes", "updated_at"])
        order.refresh_from_db()
        self.assertEqual(order.notes, "Production-only note")

    def test_approved_snapshot_stays_locked_if_source_quotation_is_deleted(self):
        self._approve()
        order = ProductionOrder.objects.get(source_quotation=self.costing)

        self.costing.delete()
        order.refresh_from_db()
        self.assertIsNone(order.source_quotation_id)
        self.assertEqual(order.approved_selling_price, Decimal("25.0000"))

        order.approved_selling_price = Decimal("99.00")
        with self.assertRaisesMessage(ValidationError, "Approved quotation pricing"):
            order.save()

    def test_approval_rolls_back_when_production_snapshot_is_invalid(self):
        self.costing.order_quantity = 0
        self.costing.save(update_fields=["order_quantity", "updated_at"])

        response = self._approve()

        self.assertEqual(response.status_code, 302)
        self.costing.refresh_from_db()
        self.assertEqual(self.costing.quotation_status, CostingHeader.QUOTATION_STATUS_DRAFT)
        self.assertFalse(ProductionOrder.objects.filter(source_quotation=self.costing).exists())

    def test_production_form_exposes_manager_but_not_approved_values(self):
        form = ProductionOrderForm(can_edit_internal_costing=True)

        self.assertIn("assigned_production_manager", form.fields)
        self.assertEqual(form.fields["bulk_deadline"].label, "Delivery date")
        for field_name in ProductionOrder.APPROVED_SNAPSHOT_FIELDS:
            self.assertNotIn(field_name.removesuffix("_id"), form.fields)

    def test_production_detail_displays_approved_quotation_snapshot(self):
        self._approve()
        order = ProductionOrder.objects.get(source_quotation=self.costing)

        response = self.client.get(reverse("production_detail", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approved Quotation Snapshot")
        self.assertContains(response, "QT20260077")
        self.assertContains(response, "CAD $25.00")
        self.assertContains(response, "CAD $2,500.00")
        self.assertContains(response, "Price locked")

    def test_requested_production_statuses_are_available(self):
        labels = dict(ProductionOrder.OPERATIONAL_STATUS_CHOICES)

        self.assertEqual(labels["planning"], "Not Started")
        self.assertEqual(labels["pattern"], "Pattern")
        self.assertEqual(labels["sample_development"], "Sample")
        self.assertEqual(labels["fabric_sourcing"], "Fabric Sourcing")
        self.assertEqual(labels["cutting"], "Cutting")
        self.assertEqual(labels["sewing"], "Sewing")
        self.assertEqual(labels["printing"], "Print / Embroidery")
        self.assertEqual(labels["finishing"], "Finishing")
        self.assertEqual(labels["qc"], "Quality Check")
        self.assertEqual(labels["ready_to_ship"], "Ready To Ship")
        self.assertEqual(labels["shipped"], "Shipped")
        self.assertEqual(labels["on_hold"], "On Hold")
        self.assertEqual(labels["cancelled"], "Cancelled")
