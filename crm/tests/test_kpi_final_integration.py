from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from crm.kpi_policy_defaults import (
    DEFAULT_NOTIFICATION_ESCALATIONS,
    DEFAULT_NOTIFICATION_EVENTS,
    ROLE_TEMPLATE_DEFINITIONS,
)
from crm.models import CRMAuditLog
from crm.models_employee import EmployeeProfile
from crm.models_kpi import KPISettings, KPITemplateVersion
from crm.models_kpi_assignments import (
    EmployeeKPIRoleAssignment,
    EmployeeKPIRoleAssignmentHistory,
)
from crm.models_kpi_bonus import KPIBonusRuleSet, KPIBonusWeightProfile
from crm.models_kpi_intelligence import KPIIntelligenceRuleSet
from crm.models_kpi_notifications import (
    KPIEscalationRule,
    KPINotificationRule,
)
from crm.models_kpi_release import KPIPolicyApproval
from crm.services.kpi_release import (
    KPIReleaseError,
    KPIReleasePermissionError,
    activate_assignment_draft,
    approve_policy,
    create_policy_successor,
    prepare_release_drafts,
    preview_assignment_plan,
    publish_policy,
    retire_policy,
    save_assignment_draft,
    submit_policy_for_review,
)


class KPIStage10FinalIntegrationTests(TestCase):
    effective_date = date(2027, 1, 1)

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.executive = user_model.objects.create_superuser(
            username="stage10-executive",
            password="test-only-password",
            email="executive@example.test",
        )
        cls.employee_user = user_model.objects.create_user(
            username="stage10-employee",
            password="test-only-password",
        )
        cls.manager_user = user_model.objects.create_user(
            username="stage10-manager",
            password="test-only-password",
        )
        cls.other_user = user_model.objects.create_user(
            username="stage10-other",
            password="test-only-password",
        )
        cls.employee = EmployeeProfile.objects.get(user=cls.employee_user)
        cls.employee.display_name = "Stage 10 Employee"
        cls.employee.status = EmployeeProfile.STATUS_ACTIVE
        cls.employee.save()
        cls.manager = EmployeeProfile.objects.get(user=cls.manager_user)
        cls.manager.display_name = "Stage 10 Manager"
        cls.manager.status = EmployeeProfile.STATUS_ACTIVE
        cls.manager.save()
        cls.other_profile = EmployeeProfile.objects.get(user=cls.other_user)
        cls.other_profile.display_name = "Stage 10 Other"
        cls.other_profile.status = EmployeeProfile.STATUS_ACTIVE
        cls.other_profile.save()
        cls.drafts = prepare_release_drafts(
            effective_date=cls.effective_date,
            currency="CAD",
            actor=cls.executive,
        )

    def _publish(self, approval):
        approval = submit_policy_for_review(
            approval,
            actor=self.executive,
            reason="Ready for controlled review",
        )
        approval = approve_policy(
            approval,
            actor=self.executive,
            reason="Approved in isolated Stage 10 test",
        )
        return publish_policy(approval, actor=self.executive)

    def test_role_template_drafts_match_approved_content_and_weights(self):
        self.assertEqual(len(self.drafts.template_versions), 15)
        expected = {
            code: (name, tuple(items))
            for code, name, items in ROLE_TEMPLATE_DEFINITIONS
        }
        for version in self.drafts.template_versions:
            expected_name, expected_items = expected[version.template.code]
            self.assertEqual(version.template.name, expected_name)
            self.assertEqual(version.status, KPITemplateVersion.STATUS_DRAFT)
            self.assertEqual(version.version, 1)
            self.assertEqual(version.effective_start, self.effective_date)
            actual_items = tuple(
                version.items.order_by("sort_order").values_list("name", "weight")
            )
            self.assertEqual(
                actual_items,
                tuple(
                    (name, Decimal(str(weight)))
                    for name, weight in expected_items
                ),
            )
            self.assertEqual(version.active_weight_total, Decimal("100.00"))

    def test_every_template_item_has_complete_draft_policy_fields(self):
        for version in self.drafts.template_versions:
            for item in version.items.all():
                self.assertTrue(item.description)
                self.assertTrue(item.purpose)
                self.assertEqual(item.measurement_method, "manual score")
                self.assertEqual(item.target, "100")
                self.assertEqual(item.review_frequency, "monthly")
                self.assertTrue(item.data_source)
                self.assertEqual(item.green_min, Decimal("85.00"))
                self.assertEqual(item.yellow_min, Decimal("70.00"))
                self.assertEqual(item.red_max, Decimal("69.99"))
                self.assertTrue(item.evidence_required)
                self.assertTrue(item.manager_approval_required)
                self.assertTrue(item.critical_failure_rule)
                self.assertTrue(item.bonus_eligible)
                self.assertTrue(item.is_active)

    def test_all_setup_records_remain_draft_and_inactive(self):
        self.assertEqual(
            KPITemplateVersion.objects.filter(status="published").count(),
            0,
        )
        self.assertFalse(self.drafts.kpi_settings.is_active)
        self.assertEqual(
            self.drafts.bonus_weight_profile.status,
            KPIBonusWeightProfile.STATUS_DRAFT,
        )
        self.assertEqual(
            self.drafts.bonus_rule_set.status,
            KPIBonusRuleSet.STATUS_DRAFT,
        )
        self.assertEqual(
            self.drafts.intelligence_rule_set.status,
            KPIIntelligenceRuleSet.STATUS_DRAFT,
        )
        self.assertEqual(
            self.drafts.notification_rule.status,
            KPINotificationRule.STATUS_DRAFT,
        )
        self.assertEqual(
            KPIPolicyApproval.service_objects.filter(status="draft").count(),
            20,
        )

    def test_status_bonus_intelligence_and_notification_defaults(self):
        settings = self.drafts.kpi_settings
        self.assertEqual(
            (
                settings.red_min,
                settings.red_max,
                settings.yellow_min,
                settings.yellow_max,
                settings.green_min,
                settings.green_max,
            ),
            (
                Decimal("0.00"),
                Decimal("69.99"),
                Decimal("70.00"),
                Decimal("84.99"),
                Decimal("85.00"),
                Decimal("100.00"),
            ),
        )
        weights = self.drafts.bonus_weight_profile
        self.assertEqual(
            weights.individual_weight
            + weights.team_weight
            + weights.company_weight,
            Decimal("100.00"),
        )
        self.assertEqual(self.drafts.bonus_rule_set.currency, "CAD")
        self.assertIsNone(self.drafts.bonus_rule_set.bonus_cap)
        intelligence = self.drafts.intelligence_rule_set
        self.assertEqual(
            intelligence.overall_kpi_weight
            + intelligence.review_completion_weight
            + intelligence.bonus_readiness_weight
            + intelligence.improvement_weight
            + intelligence.risk_control_weight,
            Decimal("100.00"),
        )
        notification = self.drafts.notification_rule
        self.assertEqual(
            notification.enabled_event_types,
            list(DEFAULT_NOTIFICATION_EVENTS),
        )
        self.assertEqual(notification.reminder_offsets, [7, 3, 1, 0, -1, -7])
        self.assertFalse(notification.positive_recognition_enabled)
        self.assertEqual(
            notification.escalation_rules.count(),
            len(DEFAULT_NOTIFICATION_ESCALATIONS),
        )

    def test_setup_is_idempotent_and_refuses_definition_drift(self):
        second = prepare_release_drafts(
            effective_date=self.effective_date,
            currency="CAD",
            actor=self.executive,
        )
        self.assertEqual(
            tuple(row.pk for row in second.template_versions),
            tuple(row.pk for row in self.drafts.template_versions),
        )
        self.assertEqual(KPITemplateVersion.objects.count(), 15)
        self.assertEqual(KPIPolicyApproval.service_objects.count(), 20)
        with self.assertRaises(KPIReleaseError):
            prepare_release_drafts(
                effective_date=self.effective_date,
                currency="USD",
                actor=self.executive,
            )

    def test_policy_workflow_blocks_skips_and_non_executive_approval(self):
        approval = self.drafts.approvals[0]
        with self.assertRaises(KPIReleaseError):
            publish_policy(approval, actor=self.executive)
        submitted = submit_policy_for_review(
            approval,
            actor=self.executive,
            reason="Submit for test review",
        )
        with self.assertRaises(KPIReleasePermissionError):
            approve_policy(
                submitted,
                actor=self.manager_user,
                reason="Unauthorized approval attempt",
            )
        approved = approve_policy(
            submitted,
            actor=self.executive,
            reason="Authorized approval",
        )
        published = publish_policy(approved, actor=self.executive)
        self.assertEqual(published.status, KPIPolicyApproval.STATUS_PUBLISHED)
        published.template_version.refresh_from_db()
        self.assertEqual(
            published.template_version.status,
            KPITemplateVersion.STATUS_PUBLISHED,
        )

    def test_published_settings_are_version_protected(self):
        approval = next(
            row
            for row in self.drafts.approvals
            if row.policy_type == KPIPolicyApproval.TYPE_SETTINGS
        )
        self._publish(approval)
        settings = KPISettings.objects.get(pk=self.drafts.kpi_settings.pk)
        self.assertTrue(settings.is_active)
        settings.green_min = Decimal("86.00")
        with self.assertRaises(ValidationError):
            settings.save()
        with self.assertRaises(ValidationError):
            settings.delete()

    def test_retired_policy_remains_in_approval_history(self):
        approval = self._publish(self.drafts.approvals[0])
        retired = retire_policy(
            approval,
            actor=self.executive,
            reason="Superseded in controlled test",
        )
        self.assertEqual(retired.status, KPIPolicyApproval.STATUS_RETIRED)
        self.assertTrue(retired.retirement_reason)
        with self.assertRaises(ValidationError):
            retired.delete()

    def test_published_policy_edits_create_a_new_draft_version(self):
        published = self._publish(self.drafts.approvals[0])
        successor = create_policy_successor(
            published,
            effective_date=date(2028, 1, 1),
            actor=self.executive,
        )
        self.assertEqual(successor.status, KPIPolicyApproval.STATUS_DRAFT)
        self.assertEqual(successor.template_version.version, 2)
        self.assertEqual(
            successor.template_version.active_weight_total,
            Decimal("100.00"),
        )
        published.template_version.refresh_from_db()
        self.assertEqual(
            published.template_version.status,
            KPITemplateVersion.STATUS_PUBLISHED,
        )
        successor = submit_policy_for_review(
            successor,
            actor=self.executive,
            reason="Review successor",
        )
        successor = approve_policy(
            successor,
            actor=self.executive,
            reason="Approve successor",
        )
        with self.assertRaises(KPIReleaseError):
            publish_policy(successor, actor=self.executive)
        retire_policy(
            published,
            actor=self.executive,
            reason="Successor approved",
        )
        successor = publish_policy(successor, actor=self.executive)
        self.assertEqual(successor.status, KPIPolicyApproval.STATUS_PUBLISHED)

    def test_approval_model_rejects_direct_create_update_delete(self):
        with self.assertRaises(ValidationError):
            KPIPolicyApproval.objects.create(
                policy_type=KPIPolicyApproval.TYPE_TEMPLATE,
                template_version=self.drafts.template_versions[0],
                created_by=self.executive,
            )
        approval = self.drafts.approvals[0]
        with self.assertRaises(ValidationError):
            KPIPolicyApproval.objects.filter(pk=approval.pk).update(
                status=KPIPolicyApproval.STATUS_PUBLISHED
            )
        with self.assertRaises(ValidationError):
            KPIPolicyApproval.objects.filter(pk=approval.pk).delete()

    def test_database_rejects_approval_with_mismatched_target(self):
        ungoverned_version = KPITemplateVersion.objects.create(
            template=self.drafts.template_versions[0].template,
            version=2,
            effective_start=date(2028, 1, 1),
            created_by=self.executive,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            KPIPolicyApproval.service_objects.bulk_create(
                [
                    KPIPolicyApproval(
                        policy_type=KPIPolicyApproval.TYPE_SETTINGS,
                        template_version=ungoverned_version,
                        created_by=self.executive,
                    )
                ]
            )

    def test_assignment_preview_and_inactive_draft(self):
        template = self.drafts.template_versions[0].template
        roles = [
            {
                "kpi_template": template,
                "role_weight": Decimal("100.00"),
                "manager": self.manager_user,
                "bonus_eligible": True,
                "notes": "Private Stage 10 test note",
            }
        ]
        preview = preview_assignment_plan(
            employee=self.employee,
            assignments=roles,
            start_date=self.effective_date,
            actor=self.executive,
        )
        self.assertEqual(preview.total_weight, Decimal("100.00"))
        self.assertTrue(preview.roles[0]["items"])
        rows = save_assignment_draft(
            employee=self.employee,
            assignments=roles,
            start_date=self.effective_date,
            actor=self.executive,
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].is_active)
        self.assertEqual(
            EmployeeKPIRoleAssignmentHistory.objects.filter(
                assignment=rows[0]
            ).count(),
            1,
        )

    def test_assignment_validation_blocks_bad_total_self_manager_and_scope(self):
        template = self.drafts.template_versions[0].template
        with self.assertRaises(ValidationError):
            preview_assignment_plan(
                employee=self.employee,
                assignments=[
                    {
                        "kpi_template": template,
                        "role_weight": Decimal("90.00"),
                        "manager": self.manager_user,
                    }
                ],
                start_date=self.effective_date,
                actor=self.executive,
            )
        with self.assertRaises(ValidationError):
            preview_assignment_plan(
                employee=self.employee,
                assignments=[
                    {
                        "kpi_template": template,
                        "role_weight": Decimal("100.00"),
                        "manager": self.employee_user,
                    }
                ],
                start_date=self.effective_date,
                actor=self.executive,
            )
        with self.assertRaises(KPIReleasePermissionError):
            preview_assignment_plan(
                employee=self.employee,
                assignments=[
                    {
                        "kpi_template": template,
                        "role_weight": Decimal("100.00"),
                        "manager": self.manager_user,
                    }
                ],
                start_date=self.effective_date,
                actor=self.manager_user,
            )

    def test_assignment_activation_requires_published_template_and_confirmation_data(self):
        template_version = self.drafts.template_versions[0]
        rows = save_assignment_draft(
            employee=self.employee,
            assignments=[
                {
                    "kpi_template": template_version.template,
                    "role_weight": Decimal("100.00"),
                    "manager": self.manager_user,
                    "bonus_eligible": False,
                }
            ],
            start_date=self.effective_date,
            actor=self.executive,
        )
        with self.assertRaises(ValidationError):
            activate_assignment_draft(
                assignment_ids=[rows[0].pk],
                actor=self.executive,
                reason="Premature activation",
            )
        self._publish(template_version.release_approval)
        activated = activate_assignment_draft(
            assignment_ids=[rows[0].pk],
            actor=self.executive,
            reason="Confirmed after policy publication",
        )
        self.assertTrue(activated[0].is_active)
        self.assertFalse(activated[0].bonus_eligible)
        self.assertGreaterEqual(
            EmployeeKPIRoleAssignmentHistory.objects.filter(
                assignment=activated[0]
            ).count(),
            2,
        )

    def test_management_commands_prepare_and_preview_without_activation(self):
        output = StringIO()
        call_command(
            "prepare_kpi_release",
            actor=self.executive.username,
            effective_date=self.effective_date.isoformat(),
            currency="CAD",
            stdout=output,
        )
        self.assertIn("remain unpublished", output.getvalue())
        template = self.drafts.template_versions[0].template
        output = StringIO()
        call_command(
            "manage_kpi_assignments",
            action="preview",
            actor=self.executive.username,
            employee_id=self.employee.pk,
            start_date=self.effective_date.isoformat(),
            role=[
                f"{template.pk}:100:{self.manager_user.pk}:yes",
            ],
            stdout=output,
        )
        self.assertIn("100.00%", output.getvalue())
        self.assertFalse(
            EmployeeKPIRoleAssignment.objects.filter(
                employee=self.employee
            ).exists()
        )

    def test_policy_actions_write_minimal_audit_events(self):
        approval = self.drafts.approvals[0]
        submit_policy_for_review(
            approval,
            actor=self.executive,
            reason="Private decision context must stay in policy record",
        )
        event = CRMAuditLog.objects.filter(
            module="kpi_release",
            record_id=str(approval.pk),
            field_name="policy_submitted",
        ).get()
        self.assertNotIn("Private decision context", event.new_value)
        self.assertEqual(event.actor, self.executive)
