from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import CostingHeader, CostingLineItem, Customer, Invoice, Lead, Opportunity, OrderLifecycle


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
        self.invoice = Invoice.objects.create(
            invoice_number="INV-PERM-001",
            customer=self.customer,
            costing_header=self.costing,
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
            status="invoice",
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

    def test_restricted_lifecycle_and_dashboard_hide_profit_metrics(self):
        self.client.force_login(self.restricted)

        lifecycle_response = self.client.get(reverse("order_lifecycle_detail", args=[self.lifecycle.pk]))
        self.assertEqual(lifecycle_response.status_code, 200)
        self.assertNotContains(lifecycle_response, "Net Profit Formula")
        self.assertNotContains(lifecycle_response, "Sewing Cost")
        self.assertNotContains(lifecycle_response, "Margin")

        dashboard_response = self.client.get(reverse("main_dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertNotContains(dashboard_response, "order-lifecycle-section")
        self.assertNotContains(dashboard_response, "Lifecycle Financials")
        self.assertNotContains(dashboard_response, "Estimated profit")

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
