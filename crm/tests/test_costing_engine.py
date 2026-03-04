from decimal import Decimal

from django.test import TestCase

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
        self.assertEqual(calc["total_cost_per_piece"], Decimal("10.0000"))
        self.assertEqual(calc["fob_per_piece"], Decimal("20.0000"))

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
