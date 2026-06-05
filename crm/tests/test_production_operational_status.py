from types import SimpleNamespace

from django.test import SimpleTestCase

from crm.services.production_operational_status import (
    get_production_operational_status,
    sync_operational_status,
)


class _RelatedList:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class ProductionOperationalStatusTests(SimpleTestCase):
    def _stage(self, stage_key, status):
        return SimpleNamespace(stage_key=stage_key, status=status)

    def _shipment(self, status, shipment_type="bulk"):
        return SimpleNamespace(status=status, shipment_type=shipment_type)

    def _order(self, **overrides):
        data = {
            "title": "Operational status test order",
            "style_name": "",
            "notes": "",
            "accessories_note": "",
            "extra_order_note": "",
            "production_order_type": "bulk",
            "status": "planning",
            "fabric_required_kg": None,
            "fabric_received_kg": None,
            "stages": [],
            "shipments": [],
        }
        data.update(overrides)
        data["stages"] = _RelatedList(data["stages"])
        data["shipments"] = _RelatedList(data["shipments"])
        return SimpleNamespace(**data)

    def test_done_without_shipment_is_ready_to_ship_not_shipped(self):
        order = self._order(
            status="done",
            stages=[
                self._stage("development", "done"),
                self._stage("cutting", "done"),
                self._stage("sewing", "done"),
                self._stage("qc", "done"),
                self._stage("packing", "done"),
            ],
        )

        self.assertEqual(get_production_operational_status(order), "ready_to_ship")

    def test_packing_complete_is_ready_to_ship(self):
        order = self._order(
            status="in_progress",
            stages=[self._stage("packing", "done")],
        )

        self.assertEqual(get_production_operational_status(order), "ready_to_ship")

    def test_shipment_booked_is_shipped(self):
        order = self._order(
            status="in_progress",
            shipments=[self._shipment("booked")],
        )

        self.assertEqual(get_production_operational_status(order), "shipped")

    def test_shipment_shipped_is_shipped(self):
        order = self._order(
            status="in_progress",
            shipments=[self._shipment("shipped")],
        )

        self.assertEqual(get_production_operational_status(order), "shipped")

    def test_shipment_delivered_is_shipped(self):
        order = self._order(
            status="done",
            shipments=[self._shipment("delivered")],
        )

        self.assertEqual(get_production_operational_status(order), "shipped")

    def test_sampling_order_is_sample_development(self):
        order = self._order(production_order_type="sampling")

        self.assertEqual(get_production_operational_status(order), "sample_development")

    def test_sampling_order_with_sample_shipment_is_sample_sent(self):
        order = self._order(
            production_order_type="sampling",
            shipments=[self._shipment("shipped", shipment_type="sample")],
        )

        self.assertEqual(get_production_operational_status(order), "sample_sent")

    def test_bulk_order_defaults_to_planning(self):
        order = self._order(production_order_type="bulk")

        self.assertEqual(get_production_operational_status(order), "planning")

    def test_cancelled_order_uses_legacy_closed_lost_fallback(self):
        order = self._order(status="closed_lost")

        self.assertEqual(get_production_operational_status(order), "cancelled")

    def test_stored_operational_status_overrides_derived_status(self):
        order = self._order(
            operational_status="ready_to_ship",
            shipments=[self._shipment("delivered")],
        )

        self.assertEqual(get_production_operational_status(order), "ready_to_ship")


class _SyncOrder(SimpleNamespace):
    pk = 1

    def save(self, update_fields=None):
        self.last_update_fields = update_fields or []
        self.save_count = getattr(self, "save_count", 0) + 1


class ProductionOperationalStatusSyncTests(SimpleTestCase):
    def _stage(self, stage_key, status):
        return SimpleNamespace(stage_key=stage_key, status=status)

    def _shipment(self, status, shipment_type="bulk"):
        return SimpleNamespace(status=status, shipment_type=shipment_type)

    def _order(self, **overrides):
        data = {
            "title": "Operational sync test order",
            "style_name": "",
            "notes": "",
            "accessories_note": "",
            "extra_order_note": "",
            "production_order_type": "bulk",
            "status": "planning",
            "operational_status": "planning",
            "updated_at": None,
            "fabric_required_kg": None,
            "fabric_received_kg": None,
            "stages": [],
            "shipments": [],
        }
        data.update(overrides)
        data["stages"] = _RelatedList(data["stages"])
        data["shipments"] = _RelatedList(data["shipments"])
        return _SyncOrder(**data)

    def test_stage_change_syncs_operational_status(self):
        order = self._order(stages=[self._stage("cutting", "in_progress")])

        sync_operational_status(order)

        self.assertEqual(order.operational_status, "cutting")
        self.assertEqual(order.last_update_fields, ["operational_status", "updated_at"])

    def test_new_sampling_order_syncs_to_sample_development(self):
        order = self._order(production_order_type="sampling")

        sync_operational_status(order)

        self.assertEqual(order.operational_status, "sample_development")

    def test_shipment_creation_syncs_operational_status_to_shipped(self):
        order = self._order(shipments=[self._shipment("booked")])

        sync_operational_status(order)

        self.assertEqual(order.operational_status, "shipped")

    def test_shipment_delivered_syncs_operational_status_to_shipped(self):
        order = self._order(shipments=[self._shipment("delivered")])

        sync_operational_status(order)

        self.assertEqual(order.operational_status, "shipped")

    def test_sample_shipment_syncs_operational_status_to_sample_sent(self):
        order = self._order(
            production_order_type="sampling",
            shipments=[self._shipment("shipped", shipment_type="sample")],
        )

        sync_operational_status(order)

        self.assertEqual(order.operational_status, "sample_sent")

    def test_sample_approval_can_be_recorded_through_sync_service(self):
        order = self._order(production_order_type="sampling")

        sync_operational_status(order, explicit_status="approved")

        self.assertEqual(order.operational_status, "approved")

    def test_cancelled_order_can_be_recorded_through_sync_service(self):
        order = self._order()

        sync_operational_status(order, explicit_status="cancelled")

        self.assertEqual(order.operational_status, "cancelled")
