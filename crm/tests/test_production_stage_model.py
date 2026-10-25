from datetime import date, datetime, timezone as dt_timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.audit_context import reset_current_actor, set_current_actor
from crm.models import CRMAuditLog, ProductionOrder, ProductionStage
from crm.production_forms import ProductionStageForm


class ProductionStageTimestampTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.order = ProductionOrder.objects.create(title="Isolated stage control test")
        cls.user = get_user_model().objects.create_user(username="stage-auditor")

    def stage(self, key="printing"):
        return self.order.stages.get(stage_key=key)

    def test_started_stage_records_timezone_aware_time_and_local_date(self):
        instant = datetime(2026, 10, 25, 2, 30, tzinfo=dt_timezone.utc)
        stage = self.stage()
        with timezone.override("America/Vancouver"), patch("crm.models.timezone.now", return_value=instant):
            stage.status = "in_progress"
            stage.save(update_fields=["status"])
        stage.refresh_from_db()
        self.assertEqual(stage.actual_started_at, instant)
        self.assertEqual(stage.actual_start, date(2026, 10, 24))
        self.assertIsNone(stage.actual_completed_at)
        self.assertEqual(stage.updated_at, instant)

    def test_direct_completion_records_both_timestamps_once(self):
        stage = self.stage()
        stage.status = "done"
        stage.save(update_fields=["status"])
        stage.refresh_from_db()
        first_start, first_end = stage.actual_started_at, stage.actual_completed_at
        self.assertIsNotNone(first_start)
        self.assertEqual(first_start, first_end)
        stage.notes = "Completion details only"
        stage.save()
        stage.refresh_from_db()
        self.assertEqual(stage.actual_started_at, first_start)
        self.assertEqual(stage.actual_completed_at, first_end)

    def test_completion_of_legacy_in_progress_does_not_invent_start(self):
        stage = self.stage()
        ProductionStage.objects.filter(pk=stage.pk).update(status="in_progress")
        stage.refresh_from_db()
        stage.status = "done"
        stage.save()
        self.assertIsNone(stage.actual_started_at)
        self.assertIsNone(stage.actual_start)
        self.assertIsNotNone(stage.actual_completed_at)

    def test_historical_dates_are_preserved_without_invented_times(self):
        stage = self.stage()
        original_start, original_end = date(2020, 2, 1), date(2020, 2, 4)
        ProductionStage.objects.filter(pk=stage.pk).update(
            status="done", actual_start=original_start, actual_end=original_end,
        )
        stage.refresh_from_db()
        stage.notes = "Historical note"
        stage.save()
        stage.status = "in_progress"
        stage.save()
        stage.status = "done"
        stage.save()
        stage.refresh_from_db()
        self.assertEqual(stage.actual_start, original_start)
        self.assertEqual(stage.actual_end, original_end)
        self.assertIsNone(stage.actual_started_at)
        self.assertIsNone(stage.actual_completed_at)

    def test_old_in_progress_notes_edit_does_not_start_clock(self):
        stage = self.stage()
        ProductionStage.objects.filter(pk=stage.pk).update(status="in_progress")
        stage.refresh_from_db()
        stage.notes = "A normal update"
        stage.save()
        self.assertIsNone(stage.actual_started_at)
        self.assertIsNone(stage.actual_start)

    def test_existing_timestamps_survive_reopen_and_recompletion(self):
        stage = self.stage()
        stage.status = "done"
        stage.save()
        original = (stage.actual_started_at, stage.actual_completed_at)
        stage.status = "in_progress"
        stage.save()
        stage.status = "done"
        stage.save()
        stage.refresh_from_db()
        self.assertEqual((stage.actual_started_at, stage.actual_completed_at), original)

    def test_status_not_in_update_fields_does_not_stamp_event(self):
        stage = self.stage()
        stage.status = "done"
        stage.notes = "Only notes are saved"
        stage.save(update_fields=["notes"])
        stage.refresh_from_db()
        self.assertEqual(stage.status, "planned")
        self.assertIsNone(stage.actual_started_at)
        self.assertIsNone(stage.actual_completed_at)

    def test_planned_changes_do_not_set_actual_dates(self):
        stage = self.stage()
        form = ProductionStageForm({
            "status": "planned", "planned_start": "2026-10-26",
            "planned_end": "2026-10-27", "notes": "Plan saved",
            "actual_start": "2000-01-01", "actual_end": "2000-01-02",
            "actual_started_at": "2000-01-01T00:00:00Z",
        }, instance=stage)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        stage.refresh_from_db()
        self.assertEqual(stage.planned_start, date(2026, 10, 26))
        self.assertEqual(stage.planned_end, date(2026, 10, 27))
        self.assertIsNone(stage.actual_start)
        self.assertIsNone(stage.actual_end)
        self.assertIsNone(stage.actual_started_at)
        self.assertEqual(set(form.fields), {"status", "planned_start", "planned_end", "notes"})

    def test_planned_end_before_start_is_rejected(self):
        form = ProductionStageForm({
            "status": "planned", "planned_start": "2026-10-27",
            "planned_end": "2026-10-26", "notes": "",
        }, instance=self.stage())
        self.assertFalse(form.is_valid())
        self.assertIn("planned_end", form.errors)

    def test_all_eleven_types_are_preserved_once_on_new_standard_orders(self):
        expected = {key for key, _label in ProductionStage.STAGE_CHOICES}
        keys = list(self.order.stages.values_list("stage_key", flat=True))
        self.assertEqual(len(keys), 11)
        self.assertEqual(set(keys), expected)
        self.order.notes = "An ordinary order edit"
        self.order.save(update_fields=["notes"])
        self.assertEqual(self.order.stages.count(), 11)

    def test_local_sewing_stage_initialization_remains_unchanged(self):
        # Existing local-sewing creation validation is deliberately retained;
        # inspect the initializer with an isolated order of the existing type.
        order = ProductionOrder.objects.create(title="Local initialization test")
        order.stages.all().delete()
        order.order_type, order.factory_location = "sewing_charge", "bd"
        from crm.models import create_default_stages
        create_default_stages(ProductionOrder, order, created=True)
        self.assertEqual(order.stages.count(), 9)
        self.assertFalse(order.stages.filter(stage_key__in=["fabric", "printing"]).exists())

    def test_actor_and_exact_stage_status_change_are_audited_under_order(self):
        stage = self.stage()
        token = set_current_actor(self.user)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                stage.status = "in_progress"
                stage.save()
        finally:
            reset_current_actor(token)
        event = CRMAuditLog.objects.get(
            module="production", record_id=str(self.order.pk),
            field_name=f"stage[{stage.pk}].status",
        )
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.previous_value, "planned")
        self.assertEqual(event.new_value, "in_progress")
        self.assertEqual(event.action_type, CRMAuditLog.ACTION_STATUS_CHANGED)
        self.assertIn(f"#{stage.pk}: Printing", event.record_label)
        self.assertEqual(event.target_url, reverse("production_detail", args=[self.order.pk]))
        self.assertIsNotNone(event.created_at)

    def test_rolled_back_stage_change_does_not_persist_audit_or_dates(self):
        stage = self.stage()
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                stage.status = "done"
                stage.save()
                transaction.set_rollback(True)
        stage.refresh_from_db()
        self.assertEqual(stage.status, "planned")
        self.assertIsNone(stage.actual_started_at)
        self.assertFalse(CRMAuditLog.objects.filter(field_name=f"stage[{stage.pk}].status").exists())


class ProductionStageMigrationTests(TestCase):
    def migrate_missing(self):
        state = MigrationExecutor(connection).loader.project_state([
            ("crm", "0204_production_stage_timestamps"),
        ])
        module = import_module("crm.migrations.0204_production_stage_timestamps")
        module.add_missing_active_stages(state.apps, SimpleNamespace(connection=connection))

    def test_missing_stages_added_once_without_changing_existing_history(self):
        order = ProductionOrder.objects.create(title="Existing active migration example")
        order.stages.filter(stage_key="printing").delete()
        fabric = order.stages.get(stage_key="fabric")
        ProductionStage.objects.filter(pk=fabric.pk).update(
            notes="Original history", actual_start=date(2022, 1, 3), status="in_progress",
        )
        order_before = ProductionOrder.objects.filter(pk=order.pk).values().get()
        stages_before = list(order.stages.values())
        self.migrate_missing()
        self.migrate_missing()
        self.assertEqual(order.stages.filter(stage_key="fabric").count(), 1)
        self.assertEqual(order.stages.filter(stage_key="printing").count(), 1)
        for previous in stages_before:
            self.assertEqual(ProductionStage.objects.filter(pk=previous["id"]).values().get(), previous)
        self.assertEqual(ProductionOrder.objects.filter(pk=order.pk).values().get(), order_before)
        added = order.stages.get(stage_key="printing")
        self.assertEqual(added.status, "planned")
        self.assertIsNone(added.actual_start)
        self.assertIsNone(added.actual_started_at)
        self.assertIsNone(added.actual_completed_at)
        self.assertIn("Historical progress has not been inferred", added.notes)
        self.assertEqual(CRMAuditLog.objects.filter(field_name=f"stage[{added.pk}].record").count(), 1)

    def test_archived_finished_local_and_empty_orders_are_not_backfilled(self):
        cases = [
            {"is_archived": True}, {"status": "done"}, {"status": "closed_won"},
            {"operational_status": "ready_to_ship"}, {"operational_status": "shipped"},
            {"operational_status": "cancelled"},
            {"order_type": "sewing_charge", "factory_location": "bd"},
        ]
        orders = []
        for index, updates in enumerate(cases):
            order = ProductionOrder.objects.create(title=f"Excluded migration example {index}")
            order.stages.filter(stage_key__in=["fabric", "printing"]).delete()
            ProductionOrder.objects.filter(pk=order.pk).update(**updates)
            orders.append(order)
        empty = ProductionOrder.objects.create(title="No existing stage records")
        empty.stages.all().delete()
        self.migrate_missing()
        for order in orders + [empty]:
            self.assertFalse(order.stages.filter(stage_key__in=["fabric", "printing"]).exists())
