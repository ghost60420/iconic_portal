"""Isolated stage editing regressions; no live orders or financial data."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.template.loader import render_to_string
from django.urls import reverse

from crm.models import CRMAuditLog, Invoice, InvoicePayment, JournalEntry, ProductionOrder, ProductionStage


class ProductionStageUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.ceo = users.objects.create_superuser("stage-ceo", "stage@example.com", "test")
        cls.operator = users.objects.create_user("stage-operator")
        cls.operator.groups.add(Group.objects.get_or_create(name="Production")[0])
        cls.sales = users.objects.create_user("stage-sales")
        cls.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        cls.manager = users.objects.create_user("stage-manager")
        cls.manager.groups.add(Group.objects.get_or_create(name="Manager")[0])
        cls.order = ProductionOrder.objects.create(title="Stage controls", assigned_production_manager=cls.operator)
        cls.other_order = ProductionOrder.objects.create(title="Restricted order")
        for key, label in ProductionStage.STAGE_CHOICES:
            ProductionStage.objects.get_or_create(order=cls.order, stage_key=key, defaults={"display_name": label})

    def setUp(self):
        self.client.force_login(self.operator)

    def stage(self, key="printing"):
        return self.order.stages.get(stage_key=key)

    def edit(self, stage, **overrides):
        data = {
            "version": stage.updated_at.isoformat(), "status": stage.status,
            "planned_start": "2026-11-01", "planned_end": "2026-11-02", "notes": "Checked by employee",
        }
        data.update(overrides)
        return self.client.post(reverse("production_stage_edit", args=[stage.pk]), data, HTTP_ACCEPT="application/json")

    def test_all_stage_popups_load_correct_record_without_writes(self):
        for stage in self.order.stages.all():
            with self.subTest(stage=stage.stage_key), CaptureQueriesContext(connection) as queries:
                result = self.client.get(reverse("production_stage_edit", args=[stage.pk]), HTTP_ACCEPT="application/json")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["stage"]["id"], stage.pk)
            self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))

    def test_planned_dates_notes_and_actor_audit(self):
        stage = self.stage()
        with self.captureOnCommitCallbacks(execute=True):
            result = self.edit(stage, status="in_progress")
        self.assertEqual(result.status_code, 200)
        stage.refresh_from_db()
        self.assertEqual(str(stage.planned_start), "2026-11-01")
        self.assertEqual(str(stage.planned_end), "2026-11-02")
        self.assertEqual(stage.notes, "Checked by employee")
        self.assertIsNotNone(stage.actual_started_at)
        self.assertIsNone(stage.actual_completed_at)
        row = CRMAuditLog.objects.filter(module="production", record_id=str(self.order.pk), actor=self.operator,
                                        previous_value="planned", new_value="in_progress").get()
        self.assertIn(str(stage.pk), row.record_label)
        self.assertIsNotNone(row.created_at)

    def test_direct_completion_records_both_times_and_duplicate_is_rejected(self):
        stage = self.stage()
        self.assertEqual(self.edit(stage, status="done").status_code, 200)
        self.assertEqual(self.edit(stage, status="done").status_code, 409)
        stage.refresh_from_db()
        self.assertIsNotNone(stage.actual_started_at)
        self.assertEqual(stage.actual_started_at, stage.actual_completed_at)

    def test_actual_date_or_timestamp_injection_is_ignored(self):
        stage = self.stage()
        response = self.edit(stage, actual_start="2000-01-01", actual_end="2000-01-02",
                             actual_started_at="2000-01-01T10:00:00Z")
        self.assertEqual(response.status_code, 200)
        stage.refresh_from_db()
        self.assertIsNone(stage.actual_start)
        self.assertIsNone(stage.actual_end)
        self.assertIsNone(stage.actual_started_at)

    def test_invalid_dates_or_status_do_not_save(self):
        for values in ({"planned_end": "2026-10-01"}, {"status": "invalid"}):
            with self.subTest(values=values):
                self.assertEqual(self.edit(self.stage(), **values).status_code, 400)
        self.assertEqual(self.stage().status, "planned")

    def test_quick_repeat_cannot_complete_a_just_started_stage(self):
        stage = self.stage()
        url = reverse("production_stage_click", args=[stage.pk])
        data = {"version": stage.updated_at.isoformat(), "expected_status": "planned"}
        self.assertEqual(self.client.post(url, data, HTTP_ACCEPT="application/json").status_code, 200)
        self.assertEqual(self.client.post(url, data, HTTP_ACCEPT="application/json").status_code, 409)
        self.assertEqual(self.stage().status, "in_progress")

    def test_stale_popup_does_not_overwrite_another_employee(self):
        original = self.stage()
        self.edit(original, notes="Newer saved note")
        self.assertEqual(self.edit(original, notes="Stale note").status_code, 409)
        self.assertEqual(self.stage().notes, "Newer saved note")

    def test_competing_database_writer_returns_safe_retry_without_saving(self):
        stage = self.stage()
        with patch.object(ProductionStage, "save", side_effect=OperationalError("database is locked")):
            response = self.edit(stage, status="done")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Another employee", response.json()["message"])
        stage.refresh_from_db()
        self.assertEqual(stage.status, "planned")
        self.assertIsNone(stage.actual_started_at)

    def test_assigned_employee_cannot_access_other_order_stage(self):
        other = self.other_order.stages.first()
        self.assertEqual(self.client.get(reverse("production_stage_edit", args=[other.pk]), HTTP_ACCEPT="application/json").status_code, 404)
        self.assertEqual(self.edit(other).status_code, 404)
        self.assertEqual(self.client.get(reverse("production_next_stage", args=[self.other_order.pk])).status_code, 404)

    def test_sales_and_anonymous_cannot_update_stages(self):
        stage = self.stage()
        self.client.force_login(self.sales)
        self.assertEqual(self.edit(stage).status_code, 403)
        self.client.logout()
        self.assertEqual(self.edit(stage).status_code, 302)
        self.assertEqual(self.stage().notes, "")

    def test_ceo_and_manager_access_preserved(self):
        for user in (self.ceo, self.manager):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.edit(self.stage()).status_code, 200)

    def test_csrf_required(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post(reverse("production_stage_edit", args=[self.stage().pk]), {}).status_code, 403)

    def test_no_financial_records_or_duplicate_stages_created(self):
        models = (Invoice, InvoicePayment, JournalEntry, ProductionOrder, ProductionStage)
        before = {model.__name__: list(model.objects.values()) for model in models}
        for key in ("fabric", "printing"):
            self.assertEqual(self.edit(self.stage(key), status="done").status_code, 200)
        for model in (Invoice, InvoicePayment, JournalEntry):
            self.assertEqual(list(model.objects.values()), before[model.__name__])
        self.assertEqual(ProductionOrder.objects.count(), len(before["ProductionOrder"]))
        self.assertEqual(ProductionStage.objects.count(), len(before["ProductionStage"]))

    def test_both_sections_keep_all_stage_ids_and_no_actual_edit_inputs(self):
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("production_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({s.pk for s in response.context["stages"]}, set(self.order.stages.values_list("pk", flat=True)))
        self.assertContains(response, 'id="production-stage-dialog"')
        self.assertNotContains(response, 'name="actual_start"')
        self.assertNotContains(response, 'name="actual_end"')

    def test_next_stage_uses_automatic_timestamps(self):
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("production_next_stage", args=[self.order.pk]))
        self.assertEqual(response.status_code, 302)
        started = self.order.stages.get(status="in_progress")
        self.assertIsNotNone(started.actual_started_at)

    def test_both_stage_sections_reuse_loaded_records_without_queries(self):
        stages = list(self.order.stages.all())
        for stage in stages:
            stage.edit_version = stage.updated_at.isoformat()
        with self.assertNumQueries(0):
            html = render_to_string("crm/partials/production_stages.html", {
                "order": self.order, "stages": stages, "can_edit_production": True,
                "stage_status_choices": ProductionStage.STATUS_CHOICES,
            })
        for stage in stages:
            self.assertIn(f'data-stage-card="{stage.pk}"', html)
            self.assertIn(f'data-stage-quick="{stage.pk}"', html)
