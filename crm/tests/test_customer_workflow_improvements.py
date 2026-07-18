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
    CustomerNote,
    Invoice,
    InvoiceSettings,
    InvoicePayment,
    Lead,
    AutomationNotification,
    Opportunity,
    OrderLifecycle,
    ProductReferenceImage,
    ProductionOrder,
    QuickCosting,
    Shipment,
    SystemActivityLog,
)
from crm.services.operations_permissions import scope_production_orders, scope_sales_opportunities
from crm.services.operations_notifications import sync_operations_notifications
from crm.services.order_lifecycle import create_lifecycle_from_invoice
from crm.services.opportunity_payment_stage import (
    build_awaiting_payment_metrics,
    outstanding_balance_summary_for_opportunity,
)
from crm.services.pipeline import with_pipeline_value
from crm.services.production_payment import production_payment_requirement
from crm.services.production_integrity import broken_production_state_count
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

    def _full_package_quick_costing_with_invoice(
        self,
        opportunity,
        *,
        invoice_number="INV-QC-FULL-PACKAGE",
        paid_amount=None,
        status="paid",
        currency="CAD",
        deposit_percentage=Decimal("30.00"),
    ):
        quick = QuickCosting.objects.create(
            opportunity=opportunity,
            account_brand=self.customer.account_brand,
            buyer_name=self.customer.contact_name,
            project_name="Paid Full Package Quick Costing",
            product_type=opportunity.product_type,
            pricing_type=QuickCosting.PRICING_FULL_PACKAGE,
            quantity=opportunity.moq_units or 50,
            currency=currency,
            fabric_cost_per_kg=Decimal("10.00"),
            fabric_consumption_kg_per_piece=Decimal("0.5000"),
            making_cost_per_piece=Decimal("4.00"),
            selling_price_per_piece=Decimal("19.00"),
            salesperson=self.salesperson,
            status=QuickCosting.STATUS_INVOICED,
            approved_by=self.admin,
            approved_at=timezone.now(),
            quotation_number=f"QQT-{invoice_number}",
            quoted_at=timezone.now(),
        )
        invoice_total = Decimal(quick.quantity) * quick.selling_price_per_piece
        if paid_amount is None:
            paid_amount = invoice_total
        invoice = Invoice.objects.create(
            quick_costing=quick,
            customer=self.customer,
            opportunity=opportunity,
            invoice_number=invoice_number,
            currency=currency,
            subtotal=invoice_total,
            total_amount=invoice_total,
            paid_amount=paid_amount,
            status=status,
            deposit_percentage=deposit_percentage,
        )
        create_lifecycle_from_invoice(invoice, user=self.admin)
        return quick, invoice

    def _paid_full_package_quick_costing(self, opportunity, *, invoice_number="INV-QC-FULL-PACKAGE"):
        return self._full_package_quick_costing_with_invoice(opportunity, invoice_number=invoice_number)

    def _cmt_quick_costing_with_invoice(
        self,
        opportunity,
        *,
        invoice_number="INV-QC-CMT",
        paid_amount=None,
        invoice_status="paid",
        quick_status=None,
        deposit_percentage=Decimal("30.00"),
    ):
        quick = QuickCosting.objects.create(
            opportunity=opportunity,
            account_brand=self.customer.account_brand,
            buyer_name=self.customer.contact_name,
            project_name="Quoted CMT Quick Costing",
            product_type="Other",
            pricing_type=QuickCosting.PRICING_CMT,
            quantity=opportunity.moq_units or 50,
            currency="BDT",
            sewing_charge_per_piece_bdt=Decimal("100.00"),
            sewing_cost_per_piece_bdt=Decimal("70.00"),
            extra_local_cost_bdt=Decimal("500.00"),
            salesperson=self.salesperson,
            status=quick_status or QuickCosting.STATUS_QUOTED,
            approved_by=self.admin,
            approved_at=timezone.now(),
            quotation_number=f"QQT-{invoice_number}",
            quoted_at=timezone.now(),
        )
        invoice_total = Decimal(quick.quantity) * quick.sewing_charge_per_piece_bdt
        if paid_amount is None:
            paid_amount = invoice_total
        invoice = Invoice.objects.create(
            quick_costing=quick,
            customer=self.customer,
            opportunity=opportunity,
            invoice_number=invoice_number,
            currency="BDT",
            invoice_market="bangladesh",
            invoice_type="sewing_charge",
            subtotal=invoice_total,
            total_amount=invoice_total,
            paid_amount=paid_amount,
            status=invoice_status,
            deposit_percentage=deposit_percentage,
        )
        create_lifecycle_from_invoice(invoice, user=self.admin)
        return quick, invoice

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
        customer_count = Customer.objects.count()

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
        self.assertEqual(Customer.objects.count(), customer_count)
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

    def test_selected_customer_panel_shows_context_and_separate_currency_totals(self):
        CustomerNote.objects.create(
            customer=self.customer,
            author="Sales",
            content="Latest customer note for opportunity setup.",
        )
        Lead.objects.create(
            customer=self.customer,
            account_brand="Archive Brand",
            contact_name="Alex Buyer",
            email="alex@example.com",
            assigned_to=self.salesperson,
            source="Referral",
            source_channel="LinkedIn",
            first_touch_channel="Organic",
            product_interest="Basketball jersey",
            primary_product_type="Activewear",
            product_category="Basketball Jersey",
        )
        Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Prospecting",
            product_type="Activewear",
            product_category="Basketball Jersey",
            order_currency="CAD",
            order_value=Decimal("2500.00"),
        )
        Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Closed Won",
            product_type="Streetwear",
            product_category="Hoodie",
            order_currency="USD",
            order_value=Decimal("160000.00"),
            order_value_usd=Decimal("1200.00"),
        )
        Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Closed Lost",
            product_type="Casualwear",
            product_category="T Shirt",
            order_currency="BDT",
            order_value=Decimal("90000.00"),
        )
        Invoice.objects.create(
            invoice_number="INV-CUST-CAD",
            customer=self.customer,
            currency="CAD",
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("400.00"),
            status="partial",
        )
        Invoice.objects.create(
            invoice_number="INV-CUST-BDT",
            customer=self.customer,
            currency="BDT",
            subtotal=Decimal("50000.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("50000.00"),
            paid_amount=Decimal("10000.00"),
            status="partial",
        )

        self.client.force_login(self.sales)
        response = self.client.get(reverse("add_opportunity"), {"customer": self.customer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected Customer")
        self.assertContains(response, "Customer ID")
        self.assertContains(response, "Archive Brand")
        self.assertContains(response, "Sales Person")
        self.assertContains(response, "Referral / LinkedIn / Organic")
        self.assertContains(response, "Previous Product Interest")
        self.assertContains(response, "Basketball jersey")
        self.assertContains(response, "Latest customer note for opportunity setup.")
        self.assertContains(response, "Previous Opportunities")

        stats = response.context["customer_stats"]
        self.assertEqual(stats["total_opportunities"], 3)
        self.assertEqual(stats["open_opportunities"], 1)
        self.assertEqual(stats["won_opportunities"], 1)
        self.assertEqual(stats["lost_opportunities"], 1)
        self.assertEqual(
            [(row["currency"], row["amount"]) for row in stats["total_revenue_rows"]],
            [("CAD", Decimal("2500.00")), ("USD", Decimal("1200.00")), ("BDT", Decimal("90000.00"))],
        )
        self.assertEqual(
            [(row["currency"], row["amount"]) for row in stats["outstanding_rows"]],
            [("CAD", Decimal("600.00")), ("BDT", Decimal("40000.00"))],
        )
        self.assertEqual(len(response.context["previous_customer_opportunities"]), 3)
        self.assertIn("Customer notes: Prefers basketball sets.", response.context["selected_notes"])
        self.assertIn("Previous product interest: Basketball jersey", response.context["selected_notes"])

    def test_opportunity_detail_displays_selected_currency_and_bdt_conversion(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Prospecting",
            product_type="Activewear",
            product_category="Basketball Jersey",
            order_currency="CAD",
            order_value=Decimal("170000.00"),
            order_value_usd=Decimal("2000.00"),
            fx_rate_bdt_per_usd=Decimal("85.0000"),
            moq_units=100,
        )

        pipeline_row = with_pipeline_value(Opportunity.objects.filter(pk=opportunity.pk)).get()
        self.assertEqual(pipeline_row.pipeline_currency, "CAD")
        self.assertEqual(pipeline_row.pipeline_value, Decimal("2000.00"))

        self.client.force_login(self.sales)
        response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pipeline CAD $2,000.00")
        self.assertContains(response, "CAD $2,000.00")
        self.assertContains(response, "BDT ৳170,000.00")
        self.assertNotContains(response, "Pipeline USD $2,000.00")
        self.assertNotContains(response, "CAD 170,000.00")

    def test_customer_origin_opportunity_detail_shows_customer_context(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Prospecting",
            product_type="Activewear",
            product_category="Basketball Jersey",
        )

        self.client.force_login(self.sales)
        response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Archive Brand")
        self.assertContains(response, self.customer.customer_code)
        self.assertNotContains(response, "No linked lead")

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
        Invoice.objects.create(
            costing_header=costing,
            customer=self.customer,
            opportunity=opportunity,
            invoice_number="INV-CUST-DIRECT-ADVANCED",
            currency="CAD",
            subtotal=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("500.00"),
            status="partial",
            deposit_percentage=Decimal("30.00"),
        )

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("quick_costing_detail", args=[quick.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("production_from_opportunity", args=[opportunity.pk])).status_code, 302)
        order = ProductionOrder.objects.get(opportunity=opportunity)
        self.assertIsNone(order.lead)
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.costing_header, costing)

    def test_full_package_quick_costing_paid_invoice_moves_opportunity_to_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, invoice = self._paid_full_package_quick_costing(opportunity)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(source_quick_costing=quick)
        self.assertEqual(response["Location"], reverse("production_detail", args=[order.pk]))
        self.assertIsNone(order.lead)
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.opportunity, opportunity)
        self.assertIsNone(order.costing_header)
        self.assertEqual(order.order_type, "fob")
        self.assertEqual(order.production_order_type, "bulk")
        self.assertEqual(order.qty_total, 50)
        self.assertEqual(order.approved_currency, "CAD")
        self.assertEqual(order.approved_total_value, Decimal("950.00"))
        self.assertEqual(order.assigned_production_manager, self.salesperson)
        invoice.refresh_from_db()
        self.assertEqual(invoice.order, order)
        lifecycle = OrderLifecycle.objects.get(invoice=invoice)
        self.assertEqual(lifecycle.production_order, order)
        self.assertEqual(lifecycle.status, "production")
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, "Production")
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)

        duplicate_response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]))

        self.assertEqual(duplicate_response.status_code, 302)
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)

    def test_full_package_quick_costing_deposit_threshold_payment_matrix(self):
        scenarios = [
            ("zero", Decimal("0.00"), False, "0%"),
            ("twenty_five", Decimal("237.50"), False, "25%"),
            ("twenty_nine", Decimal("275.50"), False, "29%"),
            ("thirty", Decimal("285.00"), True, "30%"),
            ("forty_nine", Decimal("465.50"), True, "49%"),
            ("fifty", Decimal("475.00"), True, "50%"),
            ("seventy_five", Decimal("712.50"), True, "75%"),
            ("one_hundred", Decimal("950.00"), True, "100%"),
        ]

        self.client.force_login(self.admin)
        for slug, paid_amount, should_create, payment_display in scenarios:
            with self.subTest(slug=slug):
                opportunity = Opportunity.objects.create(
                    lead=None,
                    customer=self.customer,
                    assigned_to=self.salesperson,
                    stage="Proposal",
                    product_type="Activewear",
                    product_category="Basketball Jersey",
                    moq_units=50,
                )
                quick, invoice = self._full_package_quick_costing_with_invoice(
                    opportunity,
                    invoice_number=f"INV-QC-DEPOSIT-{slug.upper()}",
                    paid_amount=paid_amount,
                    status="partial" if paid_amount < Decimal("950.00") else "paid",
                    deposit_percentage=Decimal("30.00"),
                )

                response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]), follow=not should_create)

                if should_create:
                    self.assertEqual(response.status_code, 302)
                    order = ProductionOrder.objects.get(source_quick_costing=quick)
                    invoice.refresh_from_db()
                    opportunity.refresh_from_db()
                    self.assertEqual(invoice.order, order)
                    self.assertEqual(invoice.paid_amount, paid_amount)
                    self.assertEqual(invoice.balance, Decimal("950.00") - paid_amount)
                    self.assertEqual(opportunity.stage, "Production")
                    self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(
                        response,
                        f"Production requires a minimum deposit of 30%. Current payment is {payment_display}.",
                    )
                    self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())

    def test_full_package_quick_costing_cancelled_invoice_blocks_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, _invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-CANCELLED",
            paid_amount=Decimal("950.00"),
            status="cancelled",
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cancelled invoices cannot move to Production.")
        self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())

    def test_full_package_quick_costing_zero_total_invoice_blocks_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-ZERO",
            paid_amount=Decimal("0.00"),
            status="draft",
        )
        invoice.subtotal = Decimal("0.00")
        invoice.total_amount = Decimal("0.00")
        invoice.save(update_fields=["subtotal", "total_amount", "updated_at"])

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice total must be greater than 0 before moving to Production.")
        self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())

    def test_production_threshold_preserves_separate_invoice_currencies(self):
        scenarios = [
            ("CAD", QuickCosting.PRICING_FULL_PACKAGE),
            ("USD", QuickCosting.PRICING_FULL_PACKAGE),
            ("BDT", QuickCosting.PRICING_CMT),
        ]

        self.client.force_login(self.admin)
        for currency, pricing_type in scenarios:
            with self.subTest(currency=currency):
                opportunity = Opportunity.objects.create(
                    lead=None,
                    customer=self.customer,
                    assigned_to=self.salesperson,
                    stage="Proposal",
                    product_type="Other" if pricing_type == QuickCosting.PRICING_CMT else "Activewear",
                    product_category="Sewing" if pricing_type == QuickCosting.PRICING_CMT else "Basketball Jersey",
                    moq_units=50,
                )
                if pricing_type == QuickCosting.PRICING_CMT:
                    quick, invoice = self._cmt_quick_costing_with_invoice(
                        opportunity,
                        invoice_number=f"INV-QC-THRESHOLD-{currency}",
                        paid_amount=Decimal("2500.00"),
                        invoice_status="partial",
                        deposit_percentage=Decimal("30.00"),
                    )
                else:
                    quick, invoice = self._full_package_quick_costing_with_invoice(
                        opportunity,
                        invoice_number=f"INV-QC-THRESHOLD-{currency}",
                        paid_amount=Decimal("285.00"),
                        status="partial",
                        currency=currency,
                        deposit_percentage=Decimal("30.00"),
                    )

                response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]))

                self.assertEqual(response.status_code, 302)
                order = ProductionOrder.objects.get(source_quick_costing=quick)
                invoice.refresh_from_db()
                self.assertEqual(invoice.currency, currency)
                self.assertEqual(invoice.order, order)
                self.assertEqual(order.approved_currency, currency)

    def test_production_payment_requirement_calculates_remaining_deposit(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Awaiting Payment",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        _quick, invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-PROGRESS",
            paid_amount=Decimal("100.00"),
            status="partial",
        )

        progress = production_payment_requirement(invoice)

        self.assertFalse(progress["allowed"])
        self.assertEqual(progress["invoice_total_display"], "CAD $950.00")
        self.assertEqual(progress["amount_paid_display"], "CAD $100.00")
        self.assertEqual(progress["outstanding_balance_display"], "CAD $850.00")
        self.assertEqual(progress["required_percentage_display"], "30%")
        self.assertEqual(progress["required_amount_display"], "CAD $285.00")
        self.assertEqual(progress["paid_percentage_display"], "10.5%")
        self.assertEqual(progress["remaining_to_start_display"], "CAD $185.00")

    def test_quoted_cmt_quick_costing_paid_invoice_moves_opportunity_to_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Other",
            product_category="Sewing",
            moq_units=50,
        )
        quick, invoice = self._cmt_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-CMT-QUOTED-PAID",
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(source_quick_costing=quick)
        self.assertEqual(response["Location"], reverse("production_detail", args=[order.pk]))
        self.assertEqual(order.order_type, "sewing_charge")
        self.assertEqual(order.factory_location, "bd")
        self.assertEqual(order.approved_currency, "BDT")
        self.assertEqual(order.approved_total_value, Decimal("5000.0000"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.order, order)
        lifecycle = OrderLifecycle.objects.get(invoice=invoice)
        self.assertEqual(lifecycle.production_order, order)
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, "Production")

        duplicate_response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]))

        self.assertEqual(duplicate_response.status_code, 302)
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)

    def test_quoted_cmt_quick_costing_partial_invoice_blocks_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Other",
            product_category="Sewing",
            moq_units=50,
        )
        quick, invoice = self._cmt_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-CMT-QUOTED-PARTIAL",
            paid_amount=Decimal("1000.00"),
            invoice_status="partial",
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production requires a minimum deposit of 30%. Current payment is 20%.")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())
        invoice.refresh_from_db()
        opportunity.refresh_from_db()
        lifecycle = OrderLifecycle.objects.get(invoice=invoice)
        self.assertIsNone(invoice.order)
        self.assertIsNone(lifecycle.production_order)
        self.assertEqual(opportunity.stage, "Awaiting Payment")

    def test_full_package_quick_costing_partial_invoice_blocks_production(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-PARTIAL",
            paid_amount=Decimal("100.00"),
            status="partial",
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production requires a minimum deposit of 30%. Current payment is 10.5%.")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())
        invoice.refresh_from_db()
        opportunity.refresh_from_db()
        lifecycle = OrderLifecycle.objects.get(invoice=invoice)
        self.assertIsNone(invoice.order)
        self.assertIsNone(lifecycle.production_order)
        self.assertEqual(lifecycle.status, "invoice")
        self.assertEqual(opportunity.stage, "Awaiting Payment")

    def test_orphan_production_stage_with_partial_invoice_is_restored_to_awaiting_payment(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Production",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-ORPHAN",
            paid_amount=Decimal("100.00"),
            status="partial",
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production requires a minimum deposit of 30%. Current payment is 10.5%.")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())
        invoice.refresh_from_db()
        opportunity.refresh_from_db()
        lifecycle = OrderLifecycle.objects.get(invoice=invoice)
        self.assertIsNone(invoice.order)
        self.assertIsNone(lifecycle.production_order)
        self.assertEqual(opportunity.stage, "Awaiting Payment")

    def test_partial_invoice_sets_awaiting_payment_filter_and_balance_badge(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-AWAITING",
            paid_amount=Decimal("100.00"),
            status="partial",
        )
        opportunity.refresh_from_db()

        self.assertEqual(opportunity.stage, "Awaiting Payment")
        summary = outstanding_balance_summary_for_opportunity(opportunity)
        self.assertEqual(summary["display"], "CAD $850.00")

        metrics = build_awaiting_payment_metrics()
        self.assertEqual(metrics["count"], 1)
        self.assertEqual(metrics["customer_count"], 1)
        self.assertEqual(metrics["display"], "CAD $850.00")

        self.client.force_login(self.admin)
        detail_response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertContains(detail_response, "Outstanding Balance: CAD $850.00")

        list_response = self.client.get(reverse("opportunities_list"), {"status": "awaiting_payment"})
        self.assertContains(list_response, opportunity.opportunity_id)
        self.assertContains(list_response, "Awaiting Payment")

    def test_broken_production_state_badge_shows_without_production_order(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Production",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Broken Production State")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())

    def test_broken_production_state_count_and_ceo_notification_resolve(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Production",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )

        self.assertEqual(broken_production_state_count(), 1)
        result = sync_operations_notifications(force=True)

        self.assertEqual(result["error"], "")
        notification = AutomationNotification.objects.get(
            source_key=f"operations:broken_production_state:{opportunity.pk}:user:{self.admin.pk}"
        )
        self.assertEqual(notification.priority, "critical")
        self.assertFalse(notification.is_resolved)
        self.assertIn(opportunity.opportunity_id, notification.message)

        ProductionOrder.objects.create(
            opportunity=opportunity,
            customer=self.customer,
            title="Recovered Production Order",
            qty_total=50,
        )
        self.assertEqual(broken_production_state_count(), 0)
        sync_operations_notifications(force=True)

        notification.refresh_from_db()
        self.assertTrue(notification.is_resolved)

    def test_ceo_dashboard_shows_broken_production_state_count(self):
        Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Production",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("ceo_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Broken Production States")
        self.assertContains(response, "<div class=\"exec-number\">1</div>", html=True)

    def test_stage_update_to_production_requires_real_production_order(self):
        lead, opportunity = self._lead_opportunity()
        opportunity.stage = "Proposal"
        opportunity.save(update_fields=["stage"])
        quick, invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-STAGE",
            paid_amount=Decimal("100.00"),
            status="partial",
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("opportunity_detail", args=[opportunity.pk]),
            {
                "action": "update_stage",
                "stage": "Production",
                "is_open": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production requires a minimum deposit of 30%. Current payment is 2.1%.")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        self.assertFalse(ProductionOrder.objects.filter(source_quick_costing=quick).exists())
        invoice.refresh_from_db()
        opportunity.refresh_from_db()
        self.assertIsNone(invoice.order)
        self.assertEqual(opportunity.stage, "Awaiting Payment")

    def test_ceo_settings_can_reduce_deposit_threshold_for_production(self):
        InvoiceSettings.objects.create(
            default_bulk_deposit_percentage=Decimal("10.00"),
            default_bd_sewing_deposit_percentage=Decimal("10.00"),
        )
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, invoice = self._full_package_quick_costing_with_invoice(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-OVERRIDE",
            paid_amount=Decimal("100.00"),
            status="partial",
            deposit_percentage=Decimal("30.00"),
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production_from_opportunity", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(source_quick_costing=quick)
        invoice.refresh_from_db()
        opportunity.refresh_from_db()
        lifecycle = OrderLifecycle.objects.get(invoice=invoice)
        self.assertEqual(invoice.order, order)
        self.assertEqual(lifecycle.production_order, order)
        self.assertEqual(opportunity.stage, "Production")
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)

    def test_full_package_quick_costing_paid_invoice_moves_from_quick_detail(self):
        opportunity = Opportunity.objects.create(
            lead=None,
            customer=self.customer,
            assigned_to=self.salesperson,
            stage="Proposal",
            product_type="Activewear",
            product_category="Basketball Jersey",
            moq_units=50,
        )
        quick, invoice = self._paid_full_package_quick_costing(
            opportunity,
            invoice_number="INV-QC-FULL-PACKAGE-DETAIL",
        )

        self.client.force_login(self.admin)
        detail_response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Move to Production")
        move_response = self.client.post(reverse("quick_costing_convert_to_production", args=[quick.pk]))

        self.assertEqual(move_response.status_code, 302)
        order = ProductionOrder.objects.get(source_quick_costing=quick)
        self.assertEqual(move_response["Location"], reverse("production_detail", args=[order.pk]))
        invoice.refresh_from_db()
        quick.refresh_from_db()
        self.assertEqual(invoice.order, order)
        self.assertEqual(quick.status, QuickCosting.STATUS_PRODUCTION)
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)

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
