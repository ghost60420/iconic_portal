import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.forms_costing import QuickCostingForm
from crm.models import Lead, Opportunity, QuickCosting


class QuickCostingTests(TestCase):
    def _admin_user(self, username="quick-costing-admin"):
        user_model = get_user_model()
        return user_model.objects.create_superuser(
            username=username,
            email=f"{username}@example.com",
            password="test-pass",
        )

    def _opportunity(self):
        lead = Lead.objects.create(
            account_brand="Test Streetwear Co",
            contact_name="Taylor Buyer",
            email="buyer@example.com",
            product_category="Hoodie",
            primary_product_type="Streetwear",
            order_quantity="300",
        )
        return Opportunity.objects.create(
            lead=lead,
            product_category="Hoodie",
            product_type="Streetwear",
            moq_units=300,
        )

    def test_calculation_summary(self):
        quick = QuickCosting(
            buyer_name="Test Buyer",
            project_name="Fast Hoodie",
            product_type="Streetwear",
            quantity=100,
            exchange_rate_bdt_per_cad=Decimal("90.00"),
            material_cost=Decimal("500.00"),
            production_cost=Decimal("300.00"),
            other_expenses=Decimal("200.00"),
            shipping_cost=Decimal("100.00"),
            selling_price_per_piece=Decimal("15.00"),
            commission_per_piece=Decimal("1.00"),
            target_margin_percent=Decimal("20.00"),
        )

        summary = quick.calculation_summary()

        self.assertEqual(summary["total_cost"], Decimal("1100.00"))
        self.assertEqual(summary["cost_per_piece"], Decimal("11.00"))
        self.assertEqual(summary["revenue"], Decimal("1500.00"))
        self.assertEqual(summary["profit_per_piece"], Decimal("4.00"))
        self.assertEqual(summary["total_profit"], Decimal("400.00"))
        self.assertEqual(summary["profit_margin_percent"], Decimal("26.66666666666666666666666667"))
        self.assertEqual(summary["commission_total"], Decimal("100.00"))
        self.assertEqual(summary["net_profit_per_piece"], Decimal("3.00"))
        self.assertEqual(summary["net_profit_total"], Decimal("300.00"))
        self.assertEqual(summary["net_profit_margin_percent"], Decimal("20.0"))
        self.assertEqual(summary["margin_status"], "Meets target")

    def test_calculation_summary_handles_missing_exchange_and_zero_quantity(self):
        quick = QuickCosting(
            buyer_name="Test Buyer",
            project_name="Zero Quantity Safety",
            product_type="Streetwear",
            quantity=0,
            material_cost=Decimal("500.00"),
            production_cost=Decimal("300.00"),
            other_expenses=Decimal("200.00"),
            shipping_cost=Decimal("100.00"),
            selling_price_per_piece=Decimal("15.00"),
        )

        summary = quick.calculation_summary()

        self.assertIsNone(summary["exchange_rate"])
        self.assertEqual(summary["cost_per_piece"], Decimal("0"))
        self.assertEqual(summary["material_cost_per_piece"], Decimal("0"))
        self.assertEqual(summary["gross_profit_margin_percent"], Decimal("0"))
        self.assertEqual(summary["net_profit_margin_percent"], Decimal("0"))

    def test_form_blocks_zero_quantity_and_negative_cost(self):
        form = QuickCostingForm(
            data={
                "buyer_name": "Test Buyer",
                "project_name": "Fast Hoodie",
                "product_type": "Streetwear",
                "quantity": 0,
                "exchange_rate_bdt_per_cad": "0",
                "material_cost": "-1.00",
                "production_cost": "0.00",
                "other_expenses": "0.00",
                "shipping_cost": "",
                "selling_price_per_piece": "15.00",
                "commission_per_piece": "",
                "target_margin_percent": "-1",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("quantity", form.errors)
        self.assertIn("material_cost", form.errors)
        self.assertIn("exchange_rate_bdt_per_cad", form.errors)
        self.assertIn("target_margin_percent", form.errors)

    def test_detail_handles_missing_exchange_rate(self):
        admin = self._admin_user("quick-costing-no-rate-admin")
        self.client.force_login(admin)
        quick = QuickCosting.objects.create(
            buyer_name="Old Buyer",
            project_name="Legacy Quick Costing",
            product_type="Streetwear",
            quantity=100,
            material_cost=Decimal("500.00"),
            production_cost=Decimal("300.00"),
            other_expenses=Decimal("200.00"),
            shipping_cost=Decimal("100.00"),
            selling_price_per_piece=Decimal("15.00"),
        )

        response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exchange Rate")
        self.assertContains(response, "N/A")
        self.assertContains(response, "৳1,100.00 / N/A")

    def test_quick_costing_create_detail_and_list(self):
        admin = self._admin_user()
        self.client.force_login(admin)

        create_response = self.client.post(
            reverse("cost_sheet_create"),
            data={
                "costing_type": "quick",
                "buyer_name": "Test Buyer",
                "project_name": "Fast Hoodie",
                "product_type": "Streetwear",
                "quantity": 100,
                "exchange_rate_bdt_per_cad": "90.00",
                "material_cost": "500.00",
                "production_cost": "300.00",
                "other_expenses": "200.00",
                "shipping_cost": "100.00",
                "selling_price_per_piece": "15.00",
                "commission_per_piece": "1.00",
                "target_margin_percent": "20.00",
            },
        )

        quick = QuickCosting.objects.get(project_name="Fast Hoodie")
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(create_response["Location"], reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(quick.costing_type, "quick")

        detail_response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Quick Costing")
        self.assertContains(detail_response, reverse("quick_costing_export_pdf", args=[quick.pk]))
        self.assertContains(detail_response, "Shipping Cost")
        self.assertContains(detail_response, "Exchange Rate")
        self.assertContains(detail_response, "1 CAD = 90.00 BDT")
        self.assertContains(detail_response, "Gross Profit")
        self.assertContains(detail_response, "Net Profit After Commission")
        self.assertContains(detail_response, "Commission")
        self.assertContains(detail_response, "Meets target")
        self.assertContains(detail_response, "৳1,100.00 / $12.22")
        self.assertContains(detail_response, "৳300.00 / $3.33")

        pdf_response = self.client.get(reverse("quick_costing_export_pdf", args=[quick.pk]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        pdf_content = pdf_response.content

        def assert_pdf_contains(value):
            self.assertTrue(value in pdf_content, f"PDF did not contain {value!r}")

        assert_pdf_contains(b"COSTING SHEET")
        assert_pdf_contains(b"BUYER NAME")
        assert_pdf_contains(b"PROJECT NAME")
        assert_pdf_contains(b"PRODUCT TYPE")
        assert_pdf_contains(b"QUANTITY")
        assert_pdf_contains(b"EXCHANGE RATE")
        assert_pdf_contains(b"Per Piece - BDT / CAD")
        assert_pdf_contains(b"Total Order - BDT / CAD")
        assert_pdf_contains(b"Material Cost")
        assert_pdf_contains(b"Production Cost")
        assert_pdf_contains(b"Other Expenses")
        assert_pdf_contains(b"Shipping Cost")
        assert_pdf_contains(b"Total Cost")
        assert_pdf_contains(b"COST PER PIECE")
        assert_pdf_contains(b"SELLING PRICE PER PIECE")
        assert_pdf_contains(b"TOTAL ORDER VALUE")
        assert_pdf_contains(b"GROSS PROFIT PER PIECE")
        assert_pdf_contains(b"GROSS PROFIT TOTAL")
        assert_pdf_contains(b"COMMISSION PER PIECE")
        assert_pdf_contains(b"COMMISSION TOTAL")
        assert_pdf_contains(b"NET PROFIT PER PIECE")
        assert_pdf_contains(b"NET PROFIT TOTAL")
        assert_pdf_contains(b"GROSS PROFIT MARGIN")
        assert_pdf_contains(b"NET PROFIT MARGIN")
        assert_pdf_contains(b"TARGET MARGIN")
        assert_pdf_contains(b"MARGIN STATUS")
        assert_pdf_contains(b"Meets target")
        assert_pdf_contains(b"PREPARED BY")
        assert_pdf_contains(b"Thank You!")
        assert_pdf_contains(b"For Your Business")
        assert_pdf_contains(b"100.00")
        assert_pdf_contains(b"1,100.00")
        assert_pdf_contains(b"300.00")
        self.assertFalse(re.search(rb"0\.9254\d*\s+0\.2823\d*\s+0\.6", pdf_content))

        list_response = self.client.get(reverse("cost_sheet_list") + "?costing_type=quick")
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Quick")
        self.assertContains(list_response, "Fast Hoodie")
        self.assertContains(list_response, "BDT / CAD")
        self.assertContains(list_response, "৳1,100.00 / $12.22")

    def test_quick_costing_can_be_created_from_opportunity(self):
        admin = self._admin_user("quick-costing-opportunity-admin")
        opportunity = self._opportunity()
        self.client.force_login(admin)

        response = self.client.post(
            reverse("cost_sheet_create_for_opportunity", args=[opportunity.pk]),
            data={
                "costing_type": "quick",
                "buyer_name": "Test Streetwear Co",
                "project_name": "Oversized Hoodie",
                "product_type": "Streetwear",
                "quantity": 300,
                "exchange_rate_bdt_per_cad": "90.00",
                "material_cost": "25000.00",
                "production_cost": "15000.00",
                "other_expenses": "2000.00",
                "shipping_cost": "5000.00",
                "selling_price_per_piece": "600.00",
                "commission_per_piece": "30.00",
                "target_margin_percent": "20.00",
            },
        )

        quick = QuickCosting.objects.get(project_name="Oversized Hoodie")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(quick.opportunity, opportunity)
        self.assertEqual(quick.account_brand, "Test Streetwear Co")
        self.assertEqual(quick.contact_name, "Taylor Buyer")

        detail_response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertContains(detail_response, opportunity.opportunity_id)
        self.assertContains(detail_response, "Account / Brand")
        self.assertContains(detail_response, "Test Streetwear Co")
        self.assertContains(detail_response, "Taylor Buyer")

    def test_opportunity_detail_lists_quick_costings_and_status(self):
        admin = self._admin_user("quick-costing-opportunity-list-admin")
        opportunity = self._opportunity()
        self.client.force_login(admin)
        quick = QuickCosting.objects.create(
            opportunity=opportunity,
            account_brand="Test Streetwear Co",
            contact_name="Taylor Buyer",
            buyer_name="Test Streetwear Co",
            project_name="Oversized Hoodie",
            product_type="Streetwear",
            quantity=300,
            exchange_rate_bdt_per_cad=Decimal("90.00"),
            material_cost=Decimal("25000.00"),
            production_cost=Decimal("15000.00"),
            other_expenses=Decimal("2000.00"),
            shipping_cost=Decimal("5000.00"),
            selling_price_per_piece=Decimal("600.00"),
            commission_per_piece=Decimal("30.00"),
            target_margin_percent=Decimal("20.00"),
            created_by=admin,
        )

        response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Costings")
        self.assertContains(response, "Quick Costing")
        self.assertContains(response, f"QC-{quick.pk}")
        self.assertContains(response, reverse("quick_costing_detail", args=[quick.pk]))
        self.assertContains(response, "৳47,000.00 / $522.22")
        self.assertContains(response, "৳180,000.00 / $2,000.00")
        self.assertContains(response, "৳124,000.00 / $1,377.78")
        self.assertContains(response, "68.89%")
