from unittest.mock import patch

from django.test import TestCase

from crm.models import ProductionOrder


class ProductionOrderCodeTests(TestCase):
    def test_order_code_is_generated_before_create_save(self):
        order = ProductionOrder(title="Auto code production order")

        self.assertEqual(order.order_code, "")

        order.save()
        order.refresh_from_db()

        self.assertTrue(order.order_code)
        self.assertTrue(order.order_code.startswith("PO"))

    def test_blank_order_codes_generate_unique_values(self):
        first = ProductionOrder.objects.create(title="First auto code order")
        second = ProductionOrder.objects.create(title="Second auto code order")

        self.assertTrue(first.order_code)
        self.assertTrue(second.order_code)
        self.assertNotEqual(first.order_code, second.order_code)

    def test_supplied_order_code_is_preserved(self):
        order = ProductionOrder.objects.create(
            title="Manual code production order",
            order_code=" PO-MANUAL-001 ",
        )

        self.assertEqual(order.order_code, "PO-MANUAL-001")

    def test_generated_order_code_retries_existing_value(self):
        ProductionOrder.objects.create(
            title="Existing code production order",
            order_code="PO-COLLIDE",
        )

        with patch.object(
            ProductionOrder,
            "generate_order_code",
            side_effect=["PO-COLLIDE", "PO-UNIQUE"],
        ) as generate_order_code:
            order = ProductionOrder.objects.create(title="Retry auto code order")

        self.assertEqual(order.order_code, "PO-UNIQUE")
        self.assertEqual(generate_order_code.call_count, 2)
