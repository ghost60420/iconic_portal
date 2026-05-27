from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import CostingHeader, CostingLineItem, Customer, Lead, Opportunity
from crm.services.costing_currency import format_costing_money


class CostingCurrencyLabelTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="currency-admin",
            email="currency-admin@example.com",
            password="test-pass",
        )
        self.customer = Customer.objects.create(
            account_brand="Currency Test Brand",
            contact_name="Buyer",
            email="buyer@example.com",
            country="Canada",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Currency Test Brand",
            contact_name="Buyer",
            email="buyer@example.com",
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
            style_name="Currency Test Hoodie",
            product_type="Activewear",
            factory_location="bd",
            order_quantity=100,
            currency="CAD",
            manual_fob_per_piece=Decimal("20.00"),
        )
        CostingLineItem.objects.create(
            costing=self.costing,
            category="fabric",
            item_name="Fleece fabric",
            uom="piece",
            unit_price=Decimal("10.00"),
            consumption_value=Decimal("1.00"),
        )

    def test_costing_detail_uses_selected_currency_labels(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cost_sheet_detail", args=[self.costing.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CAD per piece")
        self.assertContains(response, "CAD total order")
        self.assertContains(response, "Freight <span data-currency-code>CAD</span>", html=True)
        self.assertNotContains(response, "BDT per piece")
        self.assertNotContains(response, "BDT total order")
        self.assertNotContains(response, "Freight BDT")

    def test_costing_list_dashboard_and_reports_include_currency(self):
        self.client.force_login(self.admin)

        list_response = self.client.get(reverse("cost_sheet_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "CAD 20.00")
        self.assertContains(list_response, "CAD")

        dashboard_response = self.client.get(reverse("cost_sheet_dashboard") + "?currency=CAD")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Avg cost per piece (CAD)")
        self.assertContains(dashboard_response, "CAD 1000.00")

        report_response = self.client.get(reverse("cost_sheet_reports"))
        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, "CAD 10.00")
        self.assertContains(report_response, "CAD 20.00")

        csv_response = self.client.get(reverse("cost_sheet_reports") + "?export=list")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(b"Style,Currency,Qty", csv_response.content)
        self.assertIn(b"Currency Test Hoodie,CAD,100", csv_response.content)

    def test_costing_money_formatter_uses_currency_code(self):
        self.assertEqual(format_costing_money(Decimal("12.345"), "USD"), "USD 12.35")
        self.assertEqual(format_costing_money(Decimal("12.345"), "CAD"), "CAD 12.35")
        self.assertEqual(format_costing_money(Decimal("12.345"), "BDT"), "BDT 12.35")
