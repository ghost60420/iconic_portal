from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    ActualCostEntry,
    CostingHeader,
    CostingLineItem,
    Customer,
    Invoice,
    Lead,
    Opportunity,
    OrderLifecycle,
    ProductionOrder,
    Shipment,
)


class InternalCostingPermissionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="internal-admin",
            email="internal-admin@example.com",
            password="test-pass",
        )
        self.allowed_staff = user_model.objects.create_user(
            username="costing-staff",
            email="costing-staff@example.com",
            password="test-pass",
            is_staff=True,
        )
        self.allowed_staff.access.can_costing = True
        self.allowed_staff.access.can_view_internal_costing = True
        self.allowed_staff.access.save()

        self.restricted = user_model.objects.create_user(
            username="restricted-costing",
            email="restricted-costing@example.com",
            password="test-pass",
            is_staff=True,
        )
        self.restricted.access.can_costing = True
        self.restricted.access.can_view_internal_costing = False
        self.restricted.access.save()

        self.customer = Customer.objects.create(
            account_brand="Permission Test Brand",
            contact_name="Buyer",
            email="buyer@example.com",
            country="Canada",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Permission Test Brand",
            contact_name="Buyer",
            email="buyer@example.com",
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            stage="Proposal",
            product_type="Activewear",
            product_category="Hoodie",
            moq_units=300,
        )
        self.costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            style_name="Permission Test Hoodie",
            product_type="Activewear",
            factory_location="bd",
            order_quantity=300,
            currency="CAD",
            manual_fob_per_piece=Decimal("20.00"),
            status="approved",
            quotation_number="QT-PERM-001",
            quoted_at=timezone.now(),
        )
        CostingLineItem.objects.create(
            costing=self.costing,
            category="fabric",
            item_name="Fleece fabric",
            uom="piece",
            unit_price=Decimal("8.00"),
            consumption_value=Decimal("1.00"),
        )
        CostingLineItem.objects.create(
            costing=self.costing,
            category="cm_labor",
            item_name="Sewing",
            uom="piece",
            unit_price=Decimal("4.00"),
            consumption_value=Decimal("1.00"),
        )
        self.production_order = ProductionOrder.objects.create(
            title="Permission Production Hoodie",
            order_code="PO-PERM-001",
            lead=self.lead,
            opportunity=self.opportunity,
            customer=self.customer,
            costing_header=self.costing,
            qty_total=300,
            status="in_progress",
            production_sewing_cost_bdt=Decimal("300.00"),
            actual_total_cost_bdt=Decimal("1200.00"),
        )
        self.actual_cost_entry = ActualCostEntry.objects.create(
            production_order=self.production_order,
            opportunity=self.opportunity,
            section="labor",
            item_name="Restricted sewing actual",
            uom="pcs",
            actual_qty_total=Decimal("300.00"),
            actual_rate=Decimal("1.00"),
            actual_total_cost=Decimal("300.00"),
        )
        self.shipment = Shipment.objects.create(
            order=self.production_order,
            opportunity=self.opportunity,
            customer=self.customer,
            carrier="dhl",
            tracking_number="PERM123",
            box_count=10,
            total_weight_kg=Decimal("120.00"),
            cost_bdt=Decimal("9000.00"),
            cost_cad=Decimal("100.00"),
            status="booked",
        )
        self.invoice = Invoice.objects.create(
            invoice_number="INV-PERM-001",
            customer=self.customer,
            costing_header=self.costing,
            order=self.production_order,
            currency="CAD",
            subtotal=Decimal("6000.00"),
            shipping_amount=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("6100.00"),
            paid_amount=Decimal("3000.00"),
            sewing_charge=Decimal("300.00"),
            other_internal_cost=Decimal("120.00"),
            internal_cost_note="Sensitive permission test note.",
            status="partial",
        )
        self.lifecycle = OrderLifecycle.objects.create(
            customer=self.customer,
            lead=self.lead,
            opportunity=self.opportunity,
            costing=self.costing,
            quotation=self.costing,
            invoice=self.invoice,
            production_order=self.production_order,
            shipping_record=self.shipment,
            status="shipping",
        )

    def test_admin_and_allowed_staff_can_open_costing_detail(self):
        for user in (self.admin, self.allowed_staff):
            self.client.force_login(user)
            response = self.client.get(reverse("cost_sheet_detail", args=[self.costing.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Profit / pc")
            self.assertContains(response, "Margin")
            list_response = self.client.get(reverse("cost_sheet_list"))
            self.assertEqual(list_response.status_code, 200)
            self.assertContains(list_response, "Profit / margin")
            dashboard_response = self.client.get(reverse("cost_sheet_dashboard"))
            self.assertEqual(dashboard_response.status_code, 200)
            self.assertContains(dashboard_response, "Top styles by profit")
            reports_response = self.client.get(reverse("cost_sheet_reports"))
            self.assertEqual(reports_response.status_code, 200)
            self.assertContains(reports_response, "Margin report CSV")
            production_response = self.client.get(reverse("production_detail", args=[self.production_order.pk]))
            self.assertEqual(production_response.status_code, 200)
            self.assertContains(production_response, "Automatic Profit Tracker")
            self.assertContains(production_response, "Restricted sewing actual")
            self.client.logout()

    def test_restricted_user_gets_403_for_costing_urls(self):
        self.client.force_login(self.restricted)
        urls = [
            reverse("cost_sheet_list"),
            reverse("cost_sheet_create"),
            reverse("cost_sheet_detail", args=[self.costing.pk]),
            reverse("cost_sheet_client_quotation", args=[self.costing.pk]),
            reverse("cost_sheet_quotation_pdf", args=[self.costing.pk]),
            reverse("cost_sheet_export_pdf", args=[self.costing.pk]),
            reverse("cost_sheet_export_excel", args=[self.costing.pk]),
            reverse("cost_sheet_dashboard"),
            reverse("cost_sheet_reports"),
            reverse("cost_sheet_guide"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_restricted_invoice_manager_cannot_see_internal_invoice_costing(self):
        self.client.force_login(self.restricted)

        detail_response = self.client.get(reverse("invoice_view", args=[self.invoice.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, "Internal Profit Estimate")
        self.assertNotContains(detail_response, "Sewing Cost")
        self.assertNotContains(detail_response, "Sensitive permission test note.")

        edit_response = self.client.get(reverse("invoice_edit", args=[self.invoice.pk]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertNotContains(edit_response, "Internal Costing")
        self.assertNotContains(edit_response, "Sewing Charge")
        self.assertNotContains(edit_response, "Other Internal Cost")

        post_response = self.client.post(
            reverse("invoice_edit", args=[self.invoice.pk]),
            {
                "order": self.production_order.pk,
                "customer": self.customer.pk,
                "invoice_number": self.invoice.invoice_number,
                "issue_date": timezone.localdate().isoformat(),
                "due_date": timezone.localdate().isoformat(),
                "currency": "CAD",
                "subtotal": "6000.00",
                "shipping_amount": "100.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "total_amount": "6100.00",
                "paid_amount": "3000.00",
                "status": "partial",
                "notes": "Restricted user invoice edit",
                "sewing_charge": "9999.00",
                "other_internal_cost": "8888.00",
                "internal_cost_note": "Leaked from restricted POST",
            },
        )
        self.assertEqual(
            post_response.status_code,
            302,
            getattr(post_response.context.get("form"), "errors", "") if post_response.context else "",
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.sewing_charge, Decimal("300.00"))
        self.assertEqual(self.invoice.other_internal_cost, Decimal("120.00"))
        self.assertEqual(self.invoice.internal_cost_note, "Sensitive permission test note.")

    def test_restricted_lifecycle_and_dashboard_hide_profit_metrics(self):
        self.client.force_login(self.restricted)

        lifecycle_response = self.client.get(reverse("order_lifecycle_detail", args=[self.lifecycle.pk]))
        self.assertEqual(lifecycle_response.status_code, 200)
        self.assertNotContains(lifecycle_response, "Net Profit Formula")
        self.assertNotContains(lifecycle_response, "Sewing Cost")
        self.assertNotContains(lifecycle_response, "Margin")
        self.assertNotContains(lifecycle_response, "6100.00")

        dashboard_response = self.client.get(reverse("main_dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertNotContains(dashboard_response, "order-lifecycle-section")
        self.assertNotContains(dashboard_response, "Lifecycle Financials")
        self.assertNotContains(dashboard_response, "Estimated profit")
        self.assertNotContains(dashboard_response, "Monthly Profit")
        self.assertNotContains(dashboard_response, "Production Profit")
        self.assertNotContains(dashboard_response, "monthly_profit")

    def test_restricted_production_pages_hide_and_protect_internal_costing(self):
        self.client.force_login(self.restricted)

        detail_response = self.client.get(reverse("production_detail", args=[self.production_order.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Restricted Section")
        self.assertNotContains(detail_response, "Automatic Profit Tracker")
        self.assertNotContains(detail_response, "Sewing Cost")
        self.assertNotContains(detail_response, "Restricted sewing actual")
        self.assertNotContains(detail_response, "Cost BDT")
        self.assertNotContains(detail_response, "Cost CAD")
        self.assertNotContains(detail_response, "Admin / Raw Details")
        self.assertNotContains(detail_response, "300.00")

        post_response = self.client.post(
            reverse("production_detail", args=[self.production_order.pk]),
            {
                "action": "add_actual",
                "section": "labor",
                "item_name": "Forbidden actual",
                "uom": "pcs",
                "actual_qty_total": "1",
                "actual_rate": "1",
                "actual_total_cost": "1",
            },
        )
        self.assertEqual(post_response.status_code, 403)

        edit_response = self.client.get(reverse("production_edit", args=[self.production_order.pk]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertNotContains(edit_response, "Sewing cost taka")
        self.assertNotContains(edit_response, "Production cost in taka")
        self.assertNotContains(edit_response, "Fabric cost per kg taka")

        tamper_response = self.client.post(
            reverse("production_edit", args=[self.production_order.pk]),
            {
                "title": self.production_order.title,
                "factory_location": self.production_order.factory_location,
                "production_order_type": self.production_order.production_order_type or "bulk",
                "operational_status": self.production_order.operational_status or "planning",
                "order_type": self.production_order.order_type,
                "lead": self.lead.pk,
                "opportunity": self.opportunity.pk,
                "customer": self.customer.pk,
                "sample_deadline": "",
                "bulk_deadline": "",
                "qty_total": "300",
                "qty_reject": "0",
                "style_name": self.production_order.style_name,
                "color_info": self.production_order.color_info,
                "size_group": self.production_order.size_group,
                "size_ratio_note": "",
                "accessories_note": "",
                "packaging_note": "",
                "extra_order_note": "",
                "fabric_required_kg": "",
                "fabric_received_kg": "",
                "fabric_used_kg": "",
                "production_sewing_cost_bdt": "9999.00",
                "actual_total_cost_bdt": "9999.00",
                "remake_required": "",
                "remake_qty": "",
                "remake_cost_bdt": "9999.00",
                "status": self.production_order.status,
                "notes": self.production_order.notes,
            },
        )
        self.assertEqual(tamper_response.status_code, 302)
        self.production_order.refresh_from_db()
        self.assertEqual(self.production_order.production_sewing_cost_bdt, Decimal("300.00"))
        self.assertEqual(self.production_order.actual_total_cost_bdt, Decimal("1200.00"))

    def test_restricted_opportunity_customer_and_shipping_hide_internal_financials(self):
        self.client.force_login(self.restricted)

        opportunity_response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertEqual(opportunity_response.status_code, 200)
        self.assertNotContains(opportunity_response, "Profit snapshot")
        self.assertNotContains(opportunity_response, "Total cost / piece")
        self.assertNotContains(opportunity_response, "Margin")
        self.assertNotContains(opportunity_response, "9000.00")

        customer_response = self.client.get(reverse("customer_detail", args=[self.customer.pk]))
        self.assertEqual(customer_response.status_code, 200)
        self.assertNotContains(customer_response, "Margin")

        shipment_detail_response = self.client.get(reverse("shipment_detail", args=[self.shipment.pk]))
        self.assertEqual(shipment_detail_response.status_code, 200)
        self.assertNotContains(shipment_detail_response, "Shipping cost")
        self.assertNotContains(shipment_detail_response, "Cost Summary")
        self.assertNotContains(shipment_detail_response, "9000.00")

        shipment_edit_response = self.client.get(reverse("shipment_edit", args=[self.shipment.pk]))
        self.assertEqual(shipment_edit_response.status_code, 200)
        self.assertNotContains(shipment_edit_response, "Shipping cost")
        self.assertNotContains(shipment_edit_response, "Cost in taka")

    def test_client_quotation_and_invoice_pdf_do_not_include_internal_fields(self):
        self.client.force_login(self.admin)

        quote_response = self.client.get(reverse("cost_sheet_client_quotation", args=[self.costing.pk]))
        self.assertEqual(quote_response.status_code, 200)
        self.assertContains(quote_response, "Quotation")
        self.assertNotContains(quote_response, "Margin")
        self.assertNotContains(quote_response, "Internal")
        self.assertNotContains(quote_response, "Sewing Cost")
        self.assertNotContains(quote_response, "Total Cost")

        invoice_pdf_response = self.client.get(reverse("invoice_pdf", args=[self.invoice.pk]))
        self.assertEqual(invoice_pdf_response.status_code, 200)
        self.assertEqual(invoice_pdf_response["Content-Type"], "application/pdf")
        self.assertNotIn(b"Sewing Cost", invoice_pdf_response.content)
        self.assertNotIn(b"Other Internal Cost", invoice_pdf_response.content)
        self.assertNotIn(b"Sensitive permission test note.", invoice_pdf_response.content)
        self.assertNotIn(b"Net Profit", invoice_pdf_response.content)
