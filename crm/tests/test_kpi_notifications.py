from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.context_processors import operations_header
from crm.models import AutomationNotification, CRMAuditLog
from crm.models_kpi_bonus import KPIBonusRuleSet, KPIBonusWeightProfile
from crm.models_kpi_intelligence import KPIIntelligenceRuleSet
from crm.models_kpi_notifications import (
    KPIAutomationRun,
    KPIEscalationRule,
    KPINotificationEvent,
    KPINotificationRule,
    KPI_NOTIFICATION_EVENT_TYPES,
    KPI_NOTIFICATION_SCHEDULES,
)
from crm.models_kpi_reviews import KPIReview
from crm.services.kpi_assignments import assign_kpi_role
from crm.services.kpi_automation import run_kpi_automation
from crm.services.kpi_bonus import create_bonus_calculation
from crm.services.kpi_notifications import (
    KPINotificationPermissionError,
    KPINotificationRequest,
    create_kpi_notifications,
    dismiss_kpi_notification,
    effective_notification_rule,
    publish_notification_rule,
    visible_kpi_notification_events,
)
from crm.services.kpi_reviews import (
    approve_kpi_review,
    create_kpi_review,
    lock_kpi_review,
    reject_kpi_review,
    save_kpi_review_draft,
    start_kpi_review,
    submit_kpi_review,
)
from crm.tests.test_kpi_performance_ui import KPIReviewTestBase


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "stage9-notification-tests",
        }
    }
)
class KPINotificationTests(KPIReviewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_assignment = assign_kpi_role(
            employee=cls.other_employee,
            kpi_template=cls.template,
            role_weight=Decimal("100.00"),
            manager=cls.other_manager,
            start_date=date(2026, 1, 1),
            actor=cls.admin,
            reason="Stage 9 scope assignment",
        )
        cls.intelligence_rules = KPIIntelligenceRuleSet.objects.create(
            code="stage9-intelligence",
            name="Stage 9 Intelligence Rules",
            version=1,
            effective_start=date(2026, 1, 1),
            minimum_score=Decimal("70.00"),
            health_green_threshold=Decimal("85.00"),
            critical_red_count=2,
            decline_percentage=Decimal("5.00"),
            trend_periods=2,
            overdue_days=1,
            review_completion_target=Decimal("90.00"),
            improvement_threshold=Decimal("5.00"),
            department_risk_threshold=Decimal("70.00"),
            manager_workload_threshold=1,
            overall_kpi_weight=Decimal("40.00"),
            review_completion_weight=Decimal("20.00"),
            bonus_readiness_weight=Decimal("15.00"),
            improvement_weight=Decimal("10.00"),
            risk_control_weight=Decimal("15.00"),
            created_by=cls.admin,
        )
        cls.intelligence_rules.status = KPIIntelligenceRuleSet.STATUS_PUBLISHED
        cls.intelligence_rules.published_by = cls.admin
        cls.intelligence_rules.save()

        cls.notification_rule = KPINotificationRule.objects.create(
            code="stage9-crm-only",
            name="Stage 9 CRM-only Notification Rules",
            version=1,
            effective_start=date(2026, 1, 1),
            enabled_event_types=sorted(KPI_NOTIFICATION_EVENT_TYPES),
            reminder_offsets=[7, 3, 1, 0, -1, -7],
            improvement_reminder_days=[1, 7],
            enabled_schedules=sorted(KPI_NOTIFICATION_SCHEDULES),
            positive_recognition_enabled=True,
            retry_limit=2,
            batch_size=100,
            source_lookback_days=365,
            created_by=cls.admin,
        )
        escalation_rows = (
            ("review_open", 0, 0, "information", ["employee", "manager"]),
            ("review_due_soon", 0, 7, "normal", ["employee", "manager"]),
            ("review_due_soon", 1, 3, "normal", ["employee", "manager"]),
            ("review_due_soon", 2, 1, "high", ["employee", "manager"]),
            ("review_due_today", 0, 0, "high", ["employee", "manager"]),
            ("review_overdue", 0, -1, "high", ["employee", "manager"]),
            ("review_overdue", 1, -7, "critical", ["manager", "director"]),
            ("approval_pending", 0, 0, "high", ["director"]),
            ("approval_pending", 1, -1, "critical", ["director", "executive"]),
            ("rejected_correction", 0, 0, "high", ["manager"]),
            ("locked_confirmation", 0, 0, "information", ["employee", "manager"]),
            ("improvement_action", 0, -1, "normal", ["employee", "manager"]),
            ("improvement_action", 1, -7, "high", ["manager", "director"]),
            ("critical_red", 0, 0, "high", ["manager", "executive"]),
            ("critical_red", 1, -7, "critical", ["director", "executive"]),
            ("yellow_attention", 0, 0, "normal", ["manager"]),
            ("green_recognition", 0, 0, "information", ["employee", "manager"]),
            ("bonus_ready", 0, 0, "information", ["manager", "executive"]),
            ("bonus_pending", 0, 0, "high", ["manager", "executive"]),
            ("bonus_blocked", 0, 0, "critical", ["manager", "executive"]),
            ("manager_queue", 0, 0, "high", ["manager", "director", "executive"]),
            ("executive_intelligence", 0, 0, "high", ["executive"]),
            ("data_quality", 0, 0, "normal", ["hr", "executive"]),
        )
        cls.escalations = {}
        for event_type, stage, offset, severity, scopes in escalation_rows:
            row = KPIEscalationRule.objects.create(
                notification_rule=cls.notification_rule,
                event_type=event_type,
                stage=stage,
                trigger_offset_days=offset,
                severity=severity,
                recipient_scopes=scopes,
            )
            cls.escalations[(event_type, stage)] = row
        cls.notification_rule = publish_notification_rule(
            cls.notification_rule,
            actor=cls.admin,
        )

    def setUp(self):
        cache.clear()

    def manual_request(
        self,
        *,
        recipient=None,
        event_type="yellow_attention",
        stage=0,
        severity=None,
        source_id="1",
        source_period="2026-07",
        source_state="state-1",
        review=None,
    ):
        escalation = self.escalations[(event_type, stage)]
        review = review
        employee = review.employee if review else None
        manager = review.manager if review else None
        return KPINotificationRequest(
            notification_rule=self.notification_rule,
            escalation_rule=escalation,
            recipient=recipient or self.manager,
            notification_type=event_type,
            severity=severity or escalation.severity,
            title="Stage 9 KPI notification",
            message="Approved KPI information requires attention.",
            action_link=(
                reverse("kpi_review_detail", args=[review.pk])
                if review
                else reverse("notification_list")
            ),
            source_event=f"test:{event_type}",
            source_record_type="kpi_review" if review else "test",
            source_record_id=str(review.pk if review else source_id),
            source_period=source_period,
            source_state=source_state,
            due_date=self.period_end,
            related_employee=employee,
            related_manager=manager,
            related_review=review,
            related_department_code="sales" if employee else "",
            related_department_name="Sales" if employee else "",
        )

    def create_other_review(self, period_type, period_start, period_end):
        return create_kpi_review(
            employee=self.employee,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            review_date=date(2026, 7, 15),
            actor=self.manager,
        )

    def create_bonus_rule(self, *, approval_required=True):
        profile = KPIBonusWeightProfile.objects.create(
            code="stage9-individual",
            name="Stage 9 Individual Weights",
            version=1,
            team_scope_code="configured-team",
            team_scope_name="Configured Team",
            individual_weight=Decimal("100.00"),
            team_weight=Decimal("0.00"),
            company_weight=Decimal("0.00"),
            effective_start=date(2026, 1, 1),
            created_by=self.admin,
        )
        profile.status = KPIBonusWeightProfile.STATUS_PUBLISHED
        profile.published_by = self.admin
        profile.save()
        rule = KPIBonusRuleSet.objects.create(
            code="stage9-individual",
            name="Stage 9 Individual Bonus",
            version=1,
            weight_profile=profile,
            bonus_enabled=True,
            minimum_score=Decimal("70.00"),
            attendance_multiplier_default=Decimal("1.0000"),
            attendance_multiplier_min=Decimal("0.0000"),
            attendance_multiplier_max=Decimal("1.2000"),
            critical_red_behavior=KPIBonusRuleSet.CRITICAL_BLOCK,
            bonus_floor=Decimal("0.00"),
            bonus_cap=Decimal("1000.00"),
            currency="CAD",
            eligible_employee_statuses=["active"],
            approval_required=approval_required,
            effective_start=date(2026, 1, 1),
            created_by=self.admin,
        )
        rule.status = KPIBonusRuleSet.STATUS_PUBLISHED
        rule.published_by = self.admin
        rule.save()
        return rule

    def test_notification_rules_are_versioned_configurable_and_immutable(self):
        self.assertEqual(
            effective_notification_rule(date(2026, 7, 1)).pk,
            self.notification_rule.pk,
        )
        self.notification_rule.reminder_offsets = [5]
        with self.assertRaises(ValidationError):
            self.notification_rule.save()
        self.assertFalse(
            KPINotificationRule.objects.exclude(pk=self.notification_rule.pk).exists()
        )

    def test_review_opening_reminder_is_recipient_scoped(self):
        review = self.create_review()
        run = run_kpi_automation(schedule="daily", as_of=self.period_start)
        self.assertEqual(run.status, KPIAutomationRun.STATUS_COMPLETED)
        event = KPINotificationEvent.service_objects.get(
            notification_type="review_open",
            recipient=self.employee_user,
            related_review=review,
        )
        self.assertIn(
            reverse("employee_performance", args=[self.employee_user.pk]),
            event.action_link,
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="review_open",
                recipient=self.manager,
                related_review=review,
            ).exists()
        )

    def test_review_due_soon_reminder(self):
        review = self.create_review()
        run_kpi_automation(schedule="daily", as_of=date(2026, 7, 24))
        event = KPINotificationEvent.service_objects.get(
            notification_type="review_due_soon",
            recipient=self.manager,
            related_review=review,
        )
        self.assertEqual(event.severity, "normal")
        self.assertEqual(event.due_date, self.period_end)

    def test_review_due_today_reminder(self):
        review = self.create_review()
        run_kpi_automation(schedule="daily", as_of=self.period_end)
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="review_due_today",
                recipient=self.employee_user,
                related_review=review,
            ).exists()
        )

    def test_review_overdue_escalates_without_duplicate(self):
        review = self.create_review()
        first = run_kpi_automation(schedule="daily", as_of=date(2026, 8, 1))
        duplicate = run_kpi_automation(schedule="daily", as_of=date(2026, 8, 1))
        escalated = run_kpi_automation(schedule="daily", as_of=date(2026, 8, 7))
        self.assertGreater(first.created_count, 0)
        self.assertGreater(duplicate.duplicate_count, 0)
        self.assertGreater(escalated.created_count, 0)
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="review_overdue",
                related_review=review,
                escalation_rule__stage=1,
                recipient=self.director,
                severity="critical",
            ).exists()
        )

    def test_approval_pending_reminder(self):
        review = self.create_review()
        self.save_review(review)
        submit_kpi_review(review, actor=self.manager)
        run_kpi_automation(schedule="daily", as_of=self.period_end)
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="approval_pending",
                recipient=self.director,
                related_review=review,
            ).exists()
        )

    def test_rejected_review_and_locked_confirmation(self):
        rejected = self.create_review()
        self.save_review(rejected)
        submit_kpi_review(rejected, actor=self.manager)
        start_kpi_review(rejected, actor=self.director)
        reject_kpi_review(
            rejected,
            actor=self.director,
            comment="Correct the approved source.",
        )
        rejected.refresh_from_db()
        run_kpi_automation(
            schedule="daily",
            as_of=timezone.localdate(rejected.rejected_at),
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="rejected_correction",
                recipient=self.manager,
                related_review=rejected,
            ).exists()
        )

    def test_locked_review_confirmation(self):
        review = self.approved_review()
        review = lock_kpi_review(review, actor=self.director)
        run_kpi_automation(
            schedule="daily",
            as_of=timezone.localdate(review.locked_at),
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="locked_confirmation",
                recipient=self.employee_user,
                related_review=review,
            ).exists()
        )

    def test_improvement_action_reminder_uses_approved_snapshot(self):
        review = self.approved_review(Decimal("75"))
        as_of = timezone.localdate(review.approved_at) + timedelta(days=1)
        run_kpi_automation(schedule="daily", as_of=as_of)
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="improvement_action",
                related_review=review,
                recipient=self.manager,
            ).exists()
        )

    def test_critical_red_alert_uses_stage8_approved_intelligence(self):
        review = self.approved_review(Decimal("95"), critical=True)
        run_kpi_automation(
            schedule="daily",
            as_of=timezone.localdate(review.approved_at),
        )
        recipients = set(
            KPINotificationEvent.service_objects.filter(
                notification_type="critical_red",
                related_review=review,
            ).values_list("recipient_id", flat=True)
        )
        self.assertEqual(recipients, {self.manager.pk, self.ceo.pk, self.admin.pk})

    def test_yellow_and_green_notifications_are_not_public_rankings(self):
        review = self.approved_review(Decimal("75"))
        run_kpi_automation(
            schedule="daily",
            as_of=timezone.localdate(review.approved_at),
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="yellow_attention",
                recipient=self.manager,
                related_review=review,
            ).exists()
        )
        self.assertFalse(
            KPINotificationEvent.service_objects.filter(
                notification_type="yellow_attention",
                recipient=self.other_employee_user,
            ).exists()
        )

    def test_green_recognition_is_optional_and_private(self):
        review = self.approved_review(Decimal("95"))
        run_kpi_automation(
            schedule="daily",
            as_of=timezone.localdate(review.approved_at),
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="green_recognition",
                recipient=self.employee_user,
                related_review=review,
            ).exists()
        )
        self.assertFalse(
            KPINotificationEvent.service_objects.filter(
                notification_type="green_recognition",
                recipient=self.other_employee_user,
            ).exists()
        )

    def test_bonus_readiness_alert_uses_immutable_result_without_amount(self):
        review = self.approved_review(Decimal("90"))
        calculation = create_bonus_calculation(
            review,
            rule_set=self.create_bonus_rule(approval_required=False),
            base_bonus_amount=Decimal("750.00"),
            actor=self.admin,
        )
        run_kpi_automation(schedule="daily", as_of=review.review_date)
        event = KPINotificationEvent.service_objects.get(
            notification_type="bonus_ready",
            source_record_id=str(calculation.pk),
            recipient=self.manager,
        )
        self.assertNotIn("750", event.message)
        self.assertFalse(
            visible_kpi_notification_events(self.employee_user).filter(
                source_record_type="kpi_bonus_calculation",
                source_record_id=str(calculation.pk),
            ).exists()
        )

    def test_bonus_pending_and_critical_blocked_alerts(self):
        review = self.approved_review(Decimal("90"))
        pending = create_bonus_calculation(
            review,
            rule_set=self.create_bonus_rule(approval_required=True),
            base_bonus_amount=Decimal("500.00"),
            actor=self.admin,
        )
        run_kpi_automation(schedule="daily", as_of=review.review_date)
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="bonus_pending",
                source_record_id=str(pending.pk),
            ).exists()
        )

    def test_bonus_blocked_alert(self):
        review = self.approved_review(Decimal("95"), critical=True)
        blocked = create_bonus_calculation(
            review,
            rule_set=self.create_bonus_rule(),
            base_bonus_amount=Decimal("500.00"),
            actor=self.admin,
        )
        run_kpi_automation(schedule="daily", as_of=review.review_date)
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="bonus_blocked",
                source_record_id=str(blocked.pk),
                severity="critical",
            ).exists()
        )

    def test_manager_queue_and_executive_alerts(self):
        self.approved_review(Decimal("90"))
        self.create_other_review(
            KPIReview.PERIOD_QUARTERLY,
            date(2026, 7, 1),
            date(2026, 9, 30),
        )
        self.create_other_review(
            KPIReview.PERIOD_ANNUAL,
            date(2026, 1, 1),
            date(2026, 12, 31),
        )
        run_kpi_automation(schedule="daily", as_of=date(2026, 7, 29))
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type="manager_queue",
                related_manager=self.manager,
                recipient=self.manager,
            ).exists()
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(
                notification_type__in=("executive_intelligence", "data_quality"),
                recipient=self.ceo,
            ).exists()
        )

    def test_weekly_monthly_quarterly_and_annual_summaries(self):
        self.approved_review(Decimal("90"))
        for schedule in ("weekly", "monthly", "quarterly", "annual"):
            run = run_kpi_automation(
                schedule=schedule,
                as_of=date(2026, 7, 29),
            )
            self.assertEqual(run.status, KPIAutomationRun.STATUS_COMPLETED)
        periods = set(
            KPINotificationEvent.service_objects.filter(
                notification_type="executive_intelligence",
                recipient=self.ceo,
            ).values_list("source_period", flat=True)
        )
        self.assertTrue(any("W" in period for period in periods))
        self.assertIn("2026-07", periods)
        self.assertIn("2026-Q3", periods)
        self.assertIn("2026", periods)

    def test_deduplication_severity_change_and_new_period(self):
        base = self.manual_request()
        first = create_kpi_notifications((base,))
        duplicate = create_kpi_notifications((base,))
        changed = create_kpi_notifications((replace_request(base, severity="high"),))
        new_period = create_kpi_notifications(
            (replace_request(base, source_period="2026-08"),)
        )
        self.assertEqual(first["created"], 1)
        self.assertEqual(duplicate["duplicates"], 1)
        self.assertEqual(changed["created"], 1)
        self.assertEqual(new_period["created"], 1)

    def test_employee_manager_director_hr_and_ceo_scope(self):
        review = self.create_review()
        requests = (
            self.manual_request(recipient=self.employee_user, review=review),
            self.manual_request(
                recipient=self.manager,
                review=review,
                source_id="2",
            ),
            self.manual_request(
                recipient=self.director,
                review=review,
                source_id="3",
            ),
            self.manual_request(recipient=self.hr, review=review, source_id="4"),
            self.manual_request(recipient=self.ceo, review=review, source_id="5"),
        )
        create_kpi_notifications(requests)
        for user in (
            self.employee_user,
            self.manager,
            self.director,
            self.hr,
            self.ceo,
        ):
            self.assertEqual(visible_kpi_notification_events(user).count(), 1)
        self.assertEqual(visible_kpi_notification_events(self.other_manager).count(), 0)

    def test_action_link_and_guessed_notification_id_are_server_scoped(self):
        review = self.create_review()
        event = create_kpi_notifications(
            (self.manual_request(recipient=self.manager, review=review),)
        )["events"][0]
        self.client.force_login(self.other_manager)
        response = self.client.get(
            reverse(
                "notification_open",
                args=[event.automation_notification_id],
            )
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse("kpi_notification_dismiss", args=[event.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_mark_read_dismiss_and_history_are_audited(self):
        event = create_kpi_notifications(
            (self.manual_request(recipient=self.manager),)
        )["events"][0]
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse(
                "notification_mark_read",
                args=[event.automation_notification_id],
            )
        )
        self.assertEqual(response.status_code, 302)
        event.automation_notification.refresh_from_db()
        self.assertTrue(event.automation_notification.is_read)
        response = self.client.post(
            reverse("kpi_notification_dismiss", args=[event.pk])
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertTrue(event.is_dismissed)
        history = self.client.get(
            reverse("notification_list"),
            {"filter": "kpi_history"},
        )
        self.assertContains(history, "Stage 9 KPI notification")
        self.assertContains(history, "Dismissed")
        actions = set(
            CRMAuditLog.objects.filter(
                module="kpi_notifications"
            ).values_list("field_name", flat=True)
        )
        self.assertTrue(
            {
                "notification_created",
                "notification_marked_read",
                "notification_dismissed",
            }.issubset(actions)
        )

    def test_kpi_history_cannot_be_deleted_from_existing_center(self):
        event = create_kpi_notifications(
            (self.manual_request(recipient=self.manager),)
        )["events"][0]
        notification = event.automation_notification
        notification.is_read = True
        notification.save(update_fields=("is_read", "updated_at"))
        self.client.force_login(self.manager)
        self.client.post(
            reverse("notification_delete_read"),
            {"notification_ids": [notification.pk]},
        )
        self.assertTrue(
            AutomationNotification.objects.filter(pk=notification.pk).exists()
        )
        self.assertTrue(
            KPINotificationEvent.service_objects.filter(pk=event.pk).exists()
        )

    def test_direct_history_edit_and_unauthorized_dismiss_are_blocked(self):
        event = create_kpi_notifications(
            (self.manual_request(recipient=self.manager),)
        )["events"][0]
        event.title = "Changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(KPINotificationPermissionError):
            dismiss_kpi_notification(event, actor=self.other_manager)

    def test_automation_success_and_command(self):
        self.create_review()
        run = run_kpi_automation(schedule="daily", as_of=self.period_start)
        self.assertEqual(run.status, KPIAutomationRun.STATUS_COMPLETED)
        output = StringIO()
        call_command(
            "run_kpi_automation",
            "--schedule",
            "daily",
            "--date",
            self.period_start.isoformat(),
            stdout=output,
        )
        self.assertIn("duplicate(s) blocked", output.getvalue())

    def test_automation_failure_retries_are_bounded_and_audited(self):
        with patch(
            "crm.services.kpi_automation._collect_candidates",
            side_effect=RuntimeError("safe test failure"),
        ) as collect:
            run = run_kpi_automation(schedule="daily", as_of=date(2026, 7, 29))
        self.assertEqual(run.status, KPIAutomationRun.STATUS_FAILED)
        self.assertEqual(run.retry_count, 2)
        self.assertEqual(collect.call_count, 3)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="kpi_notifications",
                field_name="automation_run_failed",
                record_id=str(run.pk),
            ).exists()
        )

    def test_no_external_provider_is_contacted(self):
        self.create_review()
        with patch("django.core.mail.EmailMessage.send") as send:
            run_kpi_automation(schedule="daily", as_of=self.period_start)
        send.assert_not_called()

    def test_batch_queries_do_not_grow_per_notification(self):
        def measured(count, prefix):
            requests = tuple(
                self.manual_request(
                    source_id=f"{prefix}-{index}",
                    source_state=f"state-{index}",
                )
                for index in range(count)
            )
            with CaptureQueriesContext(connection) as queries:
                create_kpi_notifications(requests)
            return len(queries)

        one = measured(1, "one")
        many = measured(50, "many")
        self.assertLessEqual(many, one + 3)

    def test_cached_header_count_uses_zero_queries(self):
        create_kpi_notifications(
            (self.manual_request(recipient=self.manager),)
        )
        request = RequestFactory().get("/")
        request.user = self.manager
        request.resolver_match = None
        operations_header(request)
        with CaptureQueriesContext(connection) as queries:
            payload = operations_header(request)
        self.assertEqual(len(queries), 0)
        self.assertEqual(payload["crm_header_unread_count"], 1)

    def test_mobile_notification_center_contains_kpi_controls(self):
        create_kpi_notifications(
            (self.manual_request(recipient=self.manager),)
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("notification_list"), {"filter": "kpi"})
        self.assertContains(response, "KPI History")
        self.assertContains(response, "Dismiss")
        self.assertContains(response, "ops-notification-card--kpi")


def replace_request(request, **changes):
    values = {
        field: getattr(request, field)
        for field in request.__dataclass_fields__
    }
    values.update(changes)
    return KPINotificationRequest(**values)
