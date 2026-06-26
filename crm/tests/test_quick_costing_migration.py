from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class QuickCostingMigrationTests(TransactionTestCase):
    migrate_from = ("crm", "0165_productionorderline_quantity")
    migrate_to = ("crm", "0166_quickcosting_detailed_currency_commission")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        old_quick_costing = old_apps.get_model("crm", "QuickCosting")
        self.legacy_id = old_quick_costing.objects.create(
            buyer_name="Legacy Buyer",
            project_name="Legacy Costing",
            product_type="Streetwear",
            quantity=100,
            material_cost=Decimal("500.00"),
            production_cost=Decimal("300.00"),
            other_expenses=Decimal("200.00"),
            shipping_cost=Decimal("100.00"),
            selling_price_per_piece=Decimal("15.00"),
            commission_per_piece=Decimal("1.00"),
        ).pk

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_quick_costing_values_remain_legacy(self):
        quick_costing = self.apps.get_model("crm", "QuickCosting").objects.get(pk=self.legacy_id)

        self.assertIsNone(quick_costing.currency)
        self.assertIsNone(quick_costing.commission_percent)
        self.assertIsNone(quick_costing.fabric_cost_per_kg)
        self.assertIsNone(quick_costing.fabric_consumption_kg_per_piece)
        self.assertEqual(quick_costing.material_cost, Decimal("500.00"))
        self.assertEqual(quick_costing.commission_per_piece, Decimal("1.00"))
