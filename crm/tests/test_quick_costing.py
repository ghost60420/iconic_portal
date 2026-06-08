from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.forms_costing import QuickCostingForm
from crm.models import QuickCosting


class QuickCostingTests(TestCase):
    def test_calculation_summary(self):
        quick = QuickCosting(
            buyer_name="Test Buyer",
            project_name="Fast Hoodie",
            product_type="Streetwear",
            quantity=100,
            material_cost=Decimal("500.00"),
            production_cost=Decimal("300.00"),
            other_expenses=Decimal("200.00"),
            shipping_cost=Decimal("100.00"),
            selling_price_per_piece=Decimal("15.00"),
        )

        summary = quick.calculation_summary()

        self.assertEqual(summary["total_cost"], Decimal("1100.00"))
        self.assertEqual(summary["cost_per_piece"], Decimal("11.00"))
        self.assertEqual(summary["revenue"], Decimal("1500.00"))
        self.assertEqual(summary["profit_per_piece"], Decimal("4.00"))
        self.assertEqual(summary["total_profit"], Decimal("400.00"))
        self.assertEqual(summary["profit_margin_percent"], Decimal("26.66666666666666666666666667"))

    def test_form_blocks_zero_quantity_and_negative_cost(self):
        form = QuickCostingForm(
            data={
                "buyer_name": "Test Buyer",
                "project_name": "Fast Hoodie",
                "product_type": "Streetwear",
                "quantity": 0,
                "material_cost": "-1.00",
                "production_cost": "0.00",
                "other_expenses": "0.00",
                "shipping_cost": "",
                "selling_price_per_piece": "15.00",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("quantity", form.errors)
        self.assertIn("material_cost", form.errors)

    def test_quick_costing_create_detail_and_list(self):
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            username="quick-costing-admin",
            email="quick-costing-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(admin)

        create_response = self.client.post(
            reverse("cost_sheet_create"),
            data={
                "costing_type": "quick",
                "buyer_name": "Test Buyer",
                "project_name": "Fast Hoodie",
                "product_type": "Streetwear",
                "quantity": 100,
                "material_cost": "500.00",
                "production_cost": "300.00",
                "other_expenses": "200.00",
                "shipping_cost": "100.00",
                "selling_price_per_piece": "15.00",
            },
        )

        quick = QuickCosting.objects.get(project_name="Fast Hoodie")
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(create_response["Location"], reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(quick.costing_type, "quick")

        detail_response = self.client.get(reverse("quick_costing_detail", args=[quick.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Quick Costing")
        self.assertContains(detail_response, "Shipping Cost")
        self.assertContains(detail_response, "Total Profit")
        self.assertContains(detail_response, "400.00")

        list_response = self.client.get(reverse("cost_sheet_list") + "?costing_type=quick")
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Quick")
        self.assertContains(list_response, "Fast Hoodie")
