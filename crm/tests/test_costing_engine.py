import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import Lead, Opportunity, Customer, CostingHeader, CostingLineItem
from crm.services.costing_engine import compute_costing


class CostingEngineTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(account_brand="Test Brand")
        self.lead = Lead.objects.create(account_brand="Test Brand")
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            opportunity_id="OPP-TEST-1",
            moq_units=100,
        )

    def test_order_uom_converts_to_per_piece(self):
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            order_quantity=100,
            currency="BDT",
            target_margin_percent=Decimal("50"),
            shipping_cost=Decimal("500.00"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="other",
            item_name="Testing",
            uom="order",
            unit_price=Decimal("1000"),
            consumption_value=Decimal("1"),
            wastage_percent=Decimal("0"),
        )

        calc = compute_costing(costing.id)
        self.assertIsNotNone(calc)
        self.assertEqual(calc["shipping_cost_order"], Decimal("500.0000"))
        self.assertEqual(calc["shipping_cost_per_piece"], Decimal("5.0000"))
        self.assertEqual(calc["total_cost_per_piece"], Decimal("15.0000"))
        self.assertEqual(calc["fob_per_piece"], Decimal("30.0000"))

    def test_denominator_converts_cone_to_piece(self):
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            order_quantity=100,
            currency="BDT",
            target_margin_percent=Decimal("20"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="sewing_trim",
            item_name="Thread",
            uom="cone",
            unit_price=Decimal("200"),
            consumption_value=Decimal("0.5"),
            denominator_value=Decimal("100"),
            wastage_percent=Decimal("0"),
        )

        calc = compute_costing(costing.id)
        self.assertIsNotNone(calc)
        self.assertEqual(calc["total_cost_per_piece"], Decimal("1.0000"))

    def test_new_categories_are_included_in_totals(self):
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            order_quantity=100,
            currency="BDT",
            target_margin_percent=Decimal("25"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="labels_branding",
            item_name="Main label set",
            uom="piece",
            unit_price=Decimal("0.30"),
            consumption_value=Decimal("1"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="wash_process",
            item_name="Garment wash",
            uom="piece",
            unit_price=Decimal("0.50"),
            consumption_value=Decimal("1"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="cm_labor",
            item_name="CM line",
            uom="piece",
            unit_price=Decimal("1.20"),
            consumption_value=Decimal("1"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="logistics_compliance",
            item_name="Testing",
            uom="order",
            unit_price=Decimal("100"),
            consumption_value=Decimal("1"),
        )

        calc = compute_costing(costing.id)

        self.assertEqual(calc["breakdown"]["labels_branding"], Decimal("0.3000"))
        self.assertEqual(calc["breakdown"]["wash_process"], Decimal("0.5000"))
        self.assertEqual(calc["breakdown"]["cm_labor"], Decimal("1.2000"))
        self.assertEqual(calc["breakdown"]["logistics_compliance"], Decimal("1.0000"))
        self.assertEqual(calc["breakdown"]["trims"], Decimal("0.3000"))
        self.assertEqual(calc["breakdown"]["other"], Decimal("1.5000"))
        self.assertEqual(calc["breakdown"]["labor"], Decimal("1.2000"))
        self.assertEqual(calc["total_cost_per_piece"], Decimal("3.0000"))

    def test_internal_pdf_includes_shipping_cost_row(self):
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            username="costing-pdf-admin",
            email="costing-pdf-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(admin)
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            order_quantity=100,
            currency="BDT",
            target_margin_percent=Decimal("30"),
            shipping_cost=Decimal("250.00"),
        )
        CostingLineItem.objects.create(
            costing=costing,
            category="fabric",
            item_name="Fleece Fabric",
            uom="piece",
            unit_price=Decimal("10.00"),
            consumption_value=Decimal("1"),
        )

        response = self.client.get(reverse("cost_sheet_export_pdf", args=[costing.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(b"COSTING SHEET", response.content)
        self.assertIn(b"Shipping Cost", response.content)
        self.assertIn(b"Total Amount", response.content)

    def test_advanced_costing_detail_save_persists_shipping_cost(self):
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            username="costing-save-admin",
            email="costing-save-admin@example.com",
            password="test-pass",
        )
        self.client.force_login(admin)
        costing = CostingHeader.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            order_quantity=100,
            currency="BDT",
            target_margin_percent=Decimal("25"),
        )

        response = self.client.post(
            reverse("cost_sheet_detail", args=[costing.pk]),
            data={
                "action": "save_costing",
                "style_name": "Shipping Test Hoodie",
                "style_code": "",
                "buyer": "Test Buyer",
                "brand": "Test Brand",
                "product_type": "Other",
                "gender": "",
                "size_range": "",
                "season": "",
                "factory_location": "bd",
                "order_quantity": "100",
                "moq": "0",
                "costing_date": "",
                "merchandiser": "",
                "currency": "BDT",
                "exchange_rate": "",
                "finance_percent_fabric": "0",
                "finance_percent_trims": "0",
                "commission_percent": "0",
                "target_margin_percent": "25",
                "manual_fob_per_piece": "",
                "shipping_cost": "250.00",
                "fabric_type": "",
                "fabric_gsm": "",
                "fabric_composition": "",
                "wash_type": "",
                "print_type": "",
                "embroidery": "",
                "label_type": "",
                "packaging_type": "",
                "special_trims": "",
                "fit_remarks": "",
                "notes": "",
                "machine_smv": "0",
                "finishing_smv": "0",
                "cpm": "0",
                "efficiency_costing": "100",
                "efficiency_planned": "100",
                "line_payload": json.dumps(
                    [
                        {
                            "category": "fabric",
                            "item_name": "Fleece",
                            "uom": "piece",
                            "unit_price": "10",
                            "freight": "0",
                            "consumption_value": "1",
                            "wastage_percent": "0",
                            "denominator_value": "1",
                        }
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        costing.refresh_from_db()
        self.assertEqual(costing.shipping_cost, Decimal("250.00"))
        calc = compute_costing(costing.id)
        self.assertEqual(calc["shipping_cost_per_piece"], Decimal("2.5000"))
        self.assertEqual(calc["total_cost_per_piece"], Decimal("12.5000"))
