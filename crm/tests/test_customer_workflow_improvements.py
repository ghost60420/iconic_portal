import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    CostingHeader,
    CostingLineItem,
    Customer,
    Invoice,
    InvoicePayment,
    Lead,
    Opportunity,
    ProductReferenceImage,
    ProductionOrder,
    QuickCosting,
    Shipment,
    SystemActivityLog,
)
from crm.services.operations_permissions import scope_production_orders, scope_sales_opportunities
from crm.services.sales_attribution import resolve_salesperson_for_record


MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class CustomerWorkflowImprovementTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="customer-admin",
            email="customer-admin@example.com",
            password="test-pass",
        )
        self.salesperson = user_model.objects.create_user(
            username="salesperson",
            email="salesperson@example.com",
            password="test-pass",
            first_name="Sales",
            last_name="Person",
        )
        self.sales = user_model.objects.create_user(
            username="sales-user",
            email="sales-user@example.com",
            password="test-pass",
        )
        access = self.sales.access
        access.can_customers = True
        access.can_opportunities = True
        access.can_costing = True
        access.can_view_internal_costing = True
        access.can_production = True
        access.save()
        self.customer = Customer.objects.create(
            account_brand="Archive Brand",
            contact_name="Alex Buyer",
            email="alex@example.com",
            phone="+1 555 0100",
            address_line1="100 Market Street",
            city="Toronto",
            country="Canada",
            notes="Prefers basketball sets.",
        )

    def _image(self, name="snapshot.jpg"):
        return SimpleUploadedFile(name, b"reference-image", content_type="image/jpeg")

    def _lead_opportunity(self):
        lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Archive Brand",
            contact_name="Alex Buyer",
            email="alex@example.com",
            assigned_to=self.salesperson,
            product_interest="Hoodie",
            primary_product_type="Activewear",
            product_category="Hoodie",
        )
        opportunity = Opportunity.objects.create(
            lead=lead,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Hoodie",
            moq_units=250,
        )
        return lead, opportunity

    def _approved_costing(self, opportunity):
        costing = CostingHeader.objects.create(
            opportunity=opportunity,
            customer=self.customer,
            style_name="Customer Direct Jersey",
            product_type=opportunity.product_type,
            factory_location="bd",
            order_quantity=opportunity.moq_units or 500,
            currency="CAD",
            manual_fob_per_piece=Decimal("20.00"),
            status="approved",
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_number="QT-CUST-DIRECT",
            quoted_by=self.admin,
            quoted_at=timezone.now(),
            quotation_approved_by=self.admin,
            quotation_approved_at=timezone.now(),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="fabric",
            item_name="Main fabric",
            uom="piece",
            unit_price=Decimal("7.00"),
            consumption_value=Decimal("1.00"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="cm_labor",
            item_name="Sewing",
            uom="piece",
            unit_price=Decimal("3.00"),
            consumption_value=Decimal("1.00"),
        )
        return costing

    def test_customer_archive_hides_active_keeps_linked_records_and_logs(self):
        lead, opportunity = self._lead_opportunity()
        order = ProductionOrder.objects.create(
            lead=lead,
            opportunity=opportunity,
            customer=self.customer,
            title="Archive Brand Production",
            qty_total=100,
        )
        invoice = Invoice.objects.create(
            invoice_number="INV-CUST-ARCHIVE",
            customer=self.customer,
            order=order,
            currency="CAD",
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("250.00"),
        )
        payment = InvoicePayment.objects.create(
            invoice=invoice,
            payment_date=timezone.localdate(),
            amount=Decimal("250.00"),
            currency="CAD",
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("customer_archive", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 302)

        self.customer.refresh_from_db()
        invoice.refresh_from_db()
        self.assertTrue(self.customer.is_archived)
        self.assertFalse(self.customer.is_active)
        self.assertEqual(Lead.objects.filter(pk=lead.pk).count(), 1)
        self.assertEqual(Opportunity.objects.filter(pk=opportunity.pk).count(), 1)
        self.assertEqual(ProductionOrder.objects.filter(pk=order.pk).count(), 1)
        self.assertEqual(invoice.total_amount, Decimal("1000.00"))
        self.assertEqual(InvoicePayment.objects.get(pk=payment.pk).amount, Decimal("250.00"))
        self.assertTrue(
            SystemActivityLog.objects.filter(
                action="archive",
                model_label="Customer",
                object_id=str(self.customer.pk),
            ).exists()
        )
        self.assertNotContains(self.client.get(reverse("customers_list")), "Archive Brand")
        self.assertContains(self.client.get(reverse("customers_list"), {"archive": "archived"}), "Archive Brand")

    def test_sales_user_cannot_archive_customer(self):
        self.client.force_login(self.sales)
        response = self.client.post(reverse("customer_archive", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_archived)
        self.assertTrue(self.customer.is_active)

    def test_create_opportunity_from_customer_without_lead(self):
        Lead.objects.create(
            customer=self.customer,
            account_brand="Archive Brand",
            assigned_to=self.salesperson,
            product_interest="Basketball jersey",
        )
        lead_count = Lead.objects.count()

        self.client.force_login(self.sales)
        response = self.client.post(
            reverse("add_opportunity"),
            {
                "customer": self.customer.pk,
                "assigned_to": self.salesperson.pk,
                "stage": "Prospecting",
                "product_type": "Activewear",
                "product_category": "Basketball Jersey",
                "moq_units": "500",
                "notes": "Customer repeat project.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lead.objects.count(), lead_count)
        opportunity = Opportunity.objects.latest("id")
        self.assertIsNone(opportunity.lead)
        self.assertEqual(opportunity.customer, self.customer)
        self.assertEqual(opportunity.assigned_to, self.salesperson)
        self.assertIn("Customer repeat project.", opportunity.notes)
        self.assertEqual(self.client.get(reverse("customer_detail", args=[self.customer.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("opportunity_detail", args=[opportunity.pk])).status_code, 200)

    def test_customer_opportunity_form_warns_about_active_opportunities(self):
        _lead, existing = self._lead_opportunity()
        self.client.force_login(self.sales)
        response = self.client.get(reverse("add_opportunity"), {"customer": self.customer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Archive Brand")
        self.assertContains(response, "Sales Person")
        self.assertContains(response, existing.opportunity_id)
        self.assertContains(response, "Existing active opportunities")

    def test_existing_lead_conversion_still_links_lead(self):
        lead = Lead.objects.create(
            account_brand="Lead Workflow Brand",
            contact_name="Lead Buyer",
            email="lead@example.com",
            assigned_to=self.salesperson,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("convert_lead_to_opportunity", args=[lead.pk]))
        self.assertEqual(response.status_code, 302)
        lead.refresh_from_db()
        opportunity = Opportunity.objects.get(lead=lead)
        self.assertEqual(opportunity.customer, lead.customer)
        self.assertEqual(opportunity.assigned_to, self.salesperson)
        self.assertEqual(lead.lead_status, "Converted")

    def test_shipment_detail_uses_product_snapshot_fallback(self):
        lead, opportunity = self._lead_opportunity()
        order = ProductionOrder.objects.create(
            lead=lead,
            opportunity=opportunity,
            customer=self.customer,
            order_code="PO-SNAPSHOT-001",
            title="Snapshot Production",
            style_name="Fleece Hoodie",
            color_info="Black",
            size_ratio_note="S-XL",
            qty_total=250,
        )
        ProductReferenceImage.objects.create(
            lead=lead,
            opportunity=opportunity,
            production_order=order,
            image=self._image(),
            caption="Approved hoodie",
        )
        shipment = Shipment.objects.create(
            order=order,
            opportunity=opportunity,
            customer=self.customer,
            carrier="dhl",
            tracking_number="SNAP123",
            status="booked",
        )
        self.client.force_login(self.sales)
        response = self.client.get(reverse("shipment_detail", args=[shipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Product Snapshot")
        self.assertContains(response, "PO-SNAPSHOT-001")
        self.assertContains(response, "250 units")
        self.assertContains(response, "Black")
        self.assertContains(response, "S-XL")

    def test_shipment_without_links_still_renders(self):
        shipment = Shipment.objects.create(carrier="dhl", tracking_number="EMPTY123", status="planned")
        self.client.force_login(self.sales)
        response = self.client.get(reverse("shipment_detail", args=[shipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No snapshot uploaded")

    def test_customer_origin_opportunity_supports_quick_costing_invoice_and_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        costing = self._approved_costing(opportunity)
        quick = QuickCosting.objects.create(
            opportunity=opportunity,
            account_brand=self.customer.account_brand,
            buyer_name=self.customer.contact_name,
            project_name="Direct Customer Quick Costing",
            product_type="Activewear",
            quantity=50,
            currency="CAD",
            fabric_cost_per_kg=Decimal("10.00"),
            fabric_consumption_kg_per_piece=Decimal("0.5000"),
            making_cost_per_piece=Decimal("4.00"),
            selling_price_per_piece=Decimal("20.00"),
            salesperson=self.salesperson,
            commission_type=QuickCosting.COMMISSION_PERCENTAGE,
            commission_value=Decimal("10.00"),
            commission_currency="CAD",
            status=QuickCosting.STATUS_APPROVED,
            approved_by=self.admin,
            approved_at=timezone.now(),
            quotation_number="QQT-CUST-DIRECT",
            quoted_at=timezone.now(),
        )
        summary = quick.calculation_summary()
        self.assertEqual(summary["gross_profit_total"], Decimal("550.000000"))
        self.assertEqual(summary["commission_total"], Decimal("55.00"))
        self.assertEqual(summary["net_profit_total"], Decimal("495.000000"))

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("quick_costing_detail", args=[quick.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("production_from_opportunity", args=[opportunity.pk])).status_code, 302)
        order = ProductionOrder.objects.get(opportunity=opportunity)
        self.assertIsNone(order.lead)
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.costing_header, costing)

    def test_commission_fixed_and_percentage_for_all_pricing_types(self):
        scenarios = [
            (QuickCosting.PRICING_FULL_PACKAGE, "CAD"),
            (QuickCosting.PRICING_FOB, "USD"),
            (QuickCosting.PRICING_CMT, "BDT"),
        ]
        for pricing_type, currency in scenarios:
            with self.subTest(pricing_type=pricing_type):
                kwargs = {
                    "buyer_name": "Commission Buyer",
                    "project_name": "Commission Project",
                    "product_type": "Activewear",
                    "pricing_type": pricing_type,
                    "quantity": 10,
                    "currency": currency,
                    "selling_price_per_piece": Decimal("100.00"),
                    "commission_type": QuickCosting.COMMISSION_PERCENTAGE,
                    "commission_value": Decimal("10.00"),
                    "commission_currency": currency,
                }
                if pricing_type == QuickCosting.PRICING_CMT:
                    kwargs.update(
                        sewing_charge_per_piece_bdt=Decimal("100.00"),
                        sewing_cost_per_piece_bdt=Decimal("60.00"),
                        extra_local_cost_bdt=Decimal("100.00"),
                    )
                else:
                    kwargs.update(
                        fabric_cost_per_kg=Decimal("20.00"),
                        fabric_consumption_kg_per_piece=Decimal("1.0000"),
                        making_cost_per_piece=Decimal("30.00"),
                    )
                percentage = QuickCosting.objects.create(**kwargs)
                percent_summary = percentage.calculation_summary()
                self.assertEqual(
                    percent_summary["commission_total"],
                    (percent_summary["gross_profit_total"] * Decimal("0.10")).quantize(Decimal("0.01")),
                )
                self.assertEqual(percent_summary["revenue"], Decimal("1000.00"))

                fixed = QuickCosting.objects.create(
                    **{
                        **kwargs,
                        "project_name": "Fixed Commission Project",
                        "commission_type": QuickCosting.COMMISSION_FIXED,
                        "commission_value": Decimal("25.00"),
                    }
                )
                self.assertEqual(fixed.calculation_summary()["commission_total"], Decimal("25.00"))

    def test_sales_scope_includes_assigned_customer_origin_opportunity(self):
        self.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.sales,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=100,
        )
        order = ProductionOrder.objects.create(
            opportunity=opportunity,
            customer=self.customer,
            title="Customer direct production",
            qty_total=100,
        )

        scoped_opportunities = scope_sales_opportunities(Opportunity.objects.all(), self.sales)
        scoped_orders = scope_production_orders(ProductionOrder.objects.all(), self.sales)
        salesperson = resolve_salesperson_for_record(opportunity)

        self.assertIn(opportunity, scoped_opportunities)
        self.assertIn(order, scoped_orders)
        self.assertEqual(salesperson["user_id"], self.sales.pk)
