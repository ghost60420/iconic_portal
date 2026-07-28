from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from crm.models import (
    EmployeeKPIRoleAssignment,
    EmployeeKPIRoleAssignmentHistory,
    EmployeeProfile,
    KPIRoleTemplate,
)
from crm.services.kpi_assignments import (
    archive_assignment,
    assign_kpi_role,
    assign_kpi_roles,
    calculate_total_active_weight,
    deactivate_assignment,
    detect_invalid_overlaps,
    get_assigned_manager,
    get_bonus_eligible_assignments,
    get_employee_kpi_template_set,
    list_assignments_for_date,
    list_current_assignments,
    update_assignment,
    update_assignments,
    validate_exact_100,
)


class EmployeeKPIAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.actor = user_model.objects.create_superuser(
            "kpi-stage3-actor",
            "kpi-stage3-actor@example.com",
            "password",
        )
        cls.employee_user = user_model.objects.create_user(
            "kpi-stage3-employee",
            first_name="Employee",
        )
        cls.manager = user_model.objects.create_user(
            "kpi-stage3-manager",
            first_name="Manager",
        )
        cls.second_manager = user_model.objects.create_user(
            "kpi-stage3-manager-two",
            first_name="Manager Two",
        )
        cls.employee = EmployeeProfile.objects.get(user=cls.employee_user)
        cls.templates = [
            KPIRoleTemplate.objects.create(
                code=f"stage3-role-{index}",
                name=f"Stage 3 Role {index}",
                created_by=cls.actor,
            )
            for index in range(1, 5)
        ]
        cls.today = timezone.localdate()

    def _single(
        self,
        *,
        employee=None,
        template=None,
        weight=Decimal("100.00"),
        manager=None,
        start_date=None,
        end_date=None,
        bonus_eligible=True,
        is_active=True,
    ):
        return assign_kpi_role(
            employee=employee or self.employee,
            kpi_template=template or self.templates[0],
            role_weight=weight,
            manager=self.manager if manager is None else manager,
            start_date=start_date or self.today,
            end_date=end_date,
            is_active=is_active,
            bonus_eligible=bonus_eligible,
            actor=self.actor,
            reason="Stage 3 test",
        )

    def _assign_many(self, weights, *, employee=None, start_date=None):
        return assign_kpi_roles(
            employee=employee or self.employee,
            assignments=[
                {
                    "kpi_template": self.templates[index],
                    "role_weight": weight,
                    "manager": self.manager,
                    "start_date": start_date or self.today,
                }
                for index, weight in enumerate(weights)
            ],
            actor=self.actor,
            reason="Stage 3 multi-role test",
        )

    def test_single_active_role_at_one_hundred_succeeds(self):
        assignment = self._single()

        self.assertTrue(assignment.is_current(self.today))
        self.assertEqual(
            calculate_total_active_weight(self.employee),
            Decimal("100.00"),
        )
        self.assertTrue(validate_exact_100(self.employee))
        self.assertEqual(assignment.assignment_version, 1)
        self.assertEqual(assignment.history_entries.count(), 1)

    def test_two_roles_totaling_one_hundred_succeed(self):
        assignments = self._assign_many(
            [Decimal("60.00"), Decimal("40.00")]
        )

        self.assertEqual(len(assignments), 2)
        self.assertEqual(list_current_assignments(self.employee).count(), 2)
        self.assertEqual(
            calculate_total_active_weight(self.employee),
            Decimal("100.00"),
        )

    def test_three_decimal_roles_totaling_one_hundred_succeed(self):
        self._assign_many(
            [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
        )

        current = list(list_current_assignments(self.employee))
        self.assertEqual(len(current), 3)
        self.assertEqual(
            sum((row.role_weight for row in current), Decimal("0.00")),
            Decimal("100.00"),
        )

    def test_current_assignment_listing_has_no_related_object_n_plus_one(self):
        self._assign_many(
            [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
        )

        with self.assertNumQueries(1):
            rows = list(list_current_assignments(self.employee))
            labels = [
                (
                    row.employee.user.username,
                    row.kpi_template.name,
                    row.manager.username,
                )
                for row in rows
            ]

        self.assertEqual(len(labels), 3)

    def test_two_roles_can_be_reweighted_atomically(self):
        assignments = self._assign_many(
            [Decimal("60.00"), Decimal("40.00")]
        )

        updated = update_assignments(
            updates=[
                {
                    "assignment": assignments[0],
                    "changes": {"role_weight": Decimal("55.00")},
                },
                {
                    "assignment": assignments[1],
                    "changes": {"role_weight": Decimal("45.00")},
                },
            ],
            actor=self.actor,
            reason="Responsibilities changed",
        )

        self.assertEqual(
            [assignment.role_weight for assignment in updated],
            [Decimal("55.00"), Decimal("45.00")],
        )
        self.assertEqual(
            calculate_total_active_weight(self.employee),
            Decimal("100.00"),
        )
        self.assertTrue(
            all(assignment.assignment_version == 2 for assignment in updated)
        )

    def test_one_sided_reweight_below_one_hundred_is_blocked(self):
        assignments = self._assign_many(
            [Decimal("60.00"), Decimal("40.00")]
        )

        with self.assertRaisesMessage(
            ValidationError,
            "must total exactly 100.00 percent",
        ):
            update_assignment(
                assignments[0],
                actor=self.actor,
                role_weight=Decimal("50.00"),
            )

        assignments[0].refresh_from_db()
        self.assertEqual(assignments[0].role_weight, Decimal("60.00"))
        self.assertEqual(assignments[0].history_entries.count(), 1)

    def test_total_below_one_hundred_is_blocked(self):
        with self.assertRaisesMessage(
            ValidationError,
            "must total exactly 100.00 percent",
        ):
            self._assign_many([Decimal("90.00")])

        self.assertFalse(
            EmployeeKPIRoleAssignment.objects.filter(employee=self.employee).exists()
        )

    def test_total_above_one_hundred_is_blocked_atomically(self):
        with self.assertRaisesMessage(
            ValidationError,
            "must total exactly 100.00 percent",
        ):
            self._assign_many([Decimal("60.00"), Decimal("50.00")])

        self.assertFalse(
            EmployeeKPIRoleAssignment.objects.filter(employee=self.employee).exists()
        )

    def test_zero_negative_and_above_one_hundred_weights_are_blocked(self):
        for weight in (
            Decimal("0.00"),
            Decimal("-0.01"),
            Decimal("100.01"),
        ):
            with self.subTest(weight=weight):
                with self.assertRaises(ValidationError):
                    self._single(weight=weight)

    def test_direct_model_save_cannot_bypass_exact_total(self):
        assignment = EmployeeKPIRoleAssignment(
            employee=self.employee,
            kpi_template=self.templates[0],
            role_weight=Decimal("90.00"),
            start_date=self.today,
            created_by=self.actor,
            updated_by=self.actor,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "must total exactly 100.00 percent",
        ):
            assignment.save()

    def test_future_assignment_is_excluded_until_start_date(self):
        start_date = self.today + timedelta(days=10)
        assignment = self._single(start_date=start_date)

        self.assertTrue(assignment.is_future(self.today))
        self.assertFalse(assignment.is_current(self.today))
        self.assertEqual(list_current_assignments(self.employee).count(), 0)
        self.assertEqual(
            list_assignments_for_date(self.employee, start_date).count(),
            1,
        )
        self.assertEqual(
            assignment.effective_weight(start_date),
            Decimal("100.00"),
        )

    def test_expired_assignment_is_excluded_but_remains_historical(self):
        start_date = self.today - timedelta(days=30)
        end_date = self.today - timedelta(days=1)
        assignment = self._single(
            start_date=start_date,
            end_date=end_date,
        )

        self.assertTrue(assignment.is_expired(self.today))
        self.assertEqual(list_current_assignments(self.employee).count(), 0)
        self.assertEqual(
            list_assignments_for_date(self.employee, start_date).count(),
            1,
        )
        self.assertTrue(
            EmployeeKPIRoleAssignment.objects.filter(pk=assignment.pk).exists()
        )

    def test_end_date_before_start_date_is_blocked(self):
        with self.assertRaisesMessage(
            ValidationError,
            "End date cannot be before start date",
        ):
            self._single(
                start_date=self.today,
                end_date=self.today - timedelta(days=1),
            )

    def test_valid_manager_and_manager_lookup(self):
        assignment = self._single()

        self.assertEqual(assignment.manager, self.manager)
        self.assertEqual(
            get_assigned_manager(
                self.employee,
                kpi_template=self.templates[0],
            ),
            self.manager,
        )

    def test_assignment_may_have_no_direct_manager(self):
        assignment = assign_kpi_role(
            employee=self.employee,
            kpi_template=self.templates[0],
            role_weight=Decimal("100.00"),
            manager=None,
            start_date=self.today,
            actor=self.actor,
            reason="Executive assignment",
        )

        self.assertIsNone(assignment.manager)
        self.assertIsNone(get_assigned_manager(self.employee))

    def test_employee_cannot_be_their_own_manager(self):
        with self.assertRaisesMessage(
            ValidationError,
            "cannot be their own KPI manager",
        ):
            self._single(manager=self.employee_user)

    def test_inactive_manager_is_blocked(self):
        inactive = get_user_model().objects.create_user(
            "kpi-stage3-inactive-manager",
            is_active=False,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "inactive user cannot be a KPI manager",
        ):
            self._single(manager=inactive)

    def test_archived_manager_profile_is_blocked(self):
        archived = get_user_model().objects.create_user(
            "kpi-stage3-archived-manager",
        )
        profile = EmployeeProfile.objects.get(user=archived)
        profile.is_archived = True
        profile.save(update_fields=["is_archived"])

        with self.assertRaisesMessage(
            ValidationError,
            "archived or missing employee profile",
        ):
            self._single(manager=archived)

    def test_manager_change_increments_version_and_records_old_and_new_values(self):
        assignment = self._single()

        assignment = update_assignment(
            assignment,
            actor=self.actor,
            manager=self.second_manager,
            reason="Reporting line changed",
        )
        histories = list(assignment.history_entries.order_by("assignment_version"))

        self.assertEqual(assignment.assignment_version, 2)
        self.assertEqual(len(histories), 2)
        self.assertEqual(histories[1].old_values["manager_id"], self.manager.pk)
        self.assertEqual(
            histories[1].new_values["manager_id"],
            self.second_manager.pk,
        )
        self.assertEqual(histories[1].changed_by, self.actor)
        self.assertEqual(histories[1].reason, "Reporting line changed")

    def test_inactive_assignment_is_excluded_and_retained_in_history(self):
        assignment = self._single()
        assignment = deactivate_assignment(
            assignment,
            actor=self.actor,
            reason="Role ended",
        )

        self.assertFalse(assignment.is_active)
        self.assertEqual(list_current_assignments(self.employee).count(), 0)
        self.assertTrue(
            EmployeeKPIRoleAssignment.objects.filter(pk=assignment.pk).exists()
        )
        self.assertEqual(assignment.history_entries.count(), 2)
        self.assertEqual(
            assignment.history_entries.order_by("-assignment_version")
            .first()
            .change_type,
            EmployeeKPIRoleAssignmentHistory.CHANGE_DEACTIVATED,
        )

    def test_archived_assignment_is_excluded_and_cannot_remain_active(self):
        assignment = self._single()
        assignment = archive_assignment(
            assignment,
            actor=self.actor,
            reason="Assignment superseded",
        )

        self.assertTrue(assignment.is_archived)
        self.assertFalse(assignment.is_active)
        self.assertEqual(list_current_assignments(self.employee).count(), 0)
        self.assertEqual(
            assignment.history_entries.order_by("-assignment_version")
            .first()
            .change_type,
            EmployeeKPIRoleAssignmentHistory.CHANGE_ARCHIVED,
        )

        assignment.is_active = True
        with self.assertRaisesMessage(
            ValidationError,
            "archived assignment cannot remain active",
        ):
            assignment.full_clean()

    def test_bonus_eligibility_filters_without_removing_performance_assignment(self):
        assignments = assign_kpi_roles(
            employee=self.employee,
            assignments=[
                {
                    "kpi_template": self.templates[0],
                    "role_weight": Decimal("60.00"),
                    "manager": self.manager,
                    "start_date": self.today,
                    "bonus_eligible": True,
                },
                {
                    "kpi_template": self.templates[1],
                    "role_weight": Decimal("40.00"),
                    "manager": self.manager,
                    "start_date": self.today,
                    "bonus_eligible": False,
                },
            ],
            actor=self.actor,
        )

        self.assertEqual(list_current_assignments(self.employee).count(), 2)
        self.assertEqual(
            list(get_bonus_eligible_assignments(self.employee)),
            [assignments[0]],
        )

    def test_bonus_eligibility_change_is_recorded(self):
        assignment = self._single()

        assignment = update_assignment(
            assignment,
            actor=self.actor,
            bonus_eligible=False,
            reason="Role remains performance-only",
        )
        history = assignment.history_entries.order_by("-assignment_version").first()

        self.assertTrue(history.old_values["bonus_eligible"])
        self.assertFalse(history.new_values["bonus_eligible"])
        self.assertEqual(get_bonus_eligible_assignments(self.employee).count(), 0)
        self.assertEqual(list_current_assignments(self.employee).count(), 1)

    def test_template_set_returns_each_current_kpi_template(self):
        self._assign_many([Decimal("60.00"), Decimal("40.00")])

        self.assertEqual(
            list(get_employee_kpi_template_set(self.employee)),
            self.templates[:2],
        )

    def test_duplicate_template_overlap_is_detected_and_blocked(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Duplicate KPI templates overlap",
        ):
            assign_kpi_roles(
                employee=self.employee,
                assignments=[
                    {
                        "kpi_template": self.templates[0],
                        "role_weight": Decimal("50.00"),
                        "start_date": self.today,
                    },
                    {
                        "kpi_template": self.templates[0],
                        "role_weight": Decimal("50.00"),
                        "start_date": self.today,
                    },
                ],
                actor=self.actor,
            )

    def test_detect_invalid_overlap_reports_future_overweight_boundary(self):
        first = EmployeeKPIRoleAssignment(
            employee=self.employee,
            kpi_template=self.templates[0],
            role_weight=Decimal("100.00"),
            start_date=self.today,
        )
        second = EmployeeKPIRoleAssignment(
            employee=self.employee,
            kpi_template=self.templates[1],
            role_weight=Decimal("50.00"),
            start_date=self.today + timedelta(days=5),
        )

        invalid = detect_invalid_overlaps(assignments=[first, second])

        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["total"], Decimal("150.00"))
        self.assertEqual(invalid[0]["date"], second.start_date)

    def test_archived_employee_cannot_receive_active_assignment(self):
        archived_user = get_user_model().objects.create_user(
            "kpi-stage3-archived-employee",
        )
        archived_employee = EmployeeProfile.objects.get(user=archived_user)
        archived_employee.is_archived = True
        archived_employee.save(update_fields=["is_archived"])

        with self.assertRaisesMessage(
            ValidationError,
            "Archived employees cannot receive active KPI assignments",
        ):
            self._single(employee=archived_employee)

    def test_missing_employee_template_and_assignment_ids_are_blocked(self):
        with self.assertRaisesMessage(
            ValidationError,
            "selected employee does not exist",
        ):
            self._single(employee=999999)

        with self.assertRaisesMessage(
            ValidationError,
            "selected KPI template does not exist",
        ):
            self._single(template=999999)

        with self.assertRaisesMessage(
            ValidationError,
            "selected assignment does not exist",
        ):
            update_assignment(999999, actor=self.actor, role_weight=Decimal("100"))

    def test_employee_and_template_identity_require_archive_and_recreate(self):
        assignment = self._single()
        other_user = get_user_model().objects.create_user(
            "kpi-stage3-other-employee",
        )
        other_employee = EmployeeProfile.objects.get(user=other_user)

        assignment.employee = other_employee
        with self.assertRaisesMessage(
            ValidationError,
            "Archive and recreate to change the employee",
        ):
            assignment.save()

        assignment.refresh_from_db()
        assignment.kpi_template = self.templates[1]
        with self.assertRaisesMessage(
            ValidationError,
            "Archive and recreate to change the KPI template",
        ):
            assignment.save()

    def test_assignment_and_history_cannot_be_deleted_or_bulk_updated(self):
        assignment = self._single()
        history = assignment.history_entries.first()

        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            assignment.delete()
        with self.assertRaisesMessage(
            ValidationError,
            "must be changed through the assignment service",
        ):
            EmployeeKPIRoleAssignment.objects.filter(pk=assignment.pk).update(
                role_weight=Decimal("90.00")
            )
        with self.assertRaisesMessage(
            ValidationError,
            "history is immutable",
        ):
            history.delete()
        history.reason = "Rewritten"
        with self.assertRaisesMessage(
            ValidationError,
            "history is immutable",
        ):
            history.save()

    def test_database_constraints_reject_invalid_row_values(self):
        invalid_rows = [
            EmployeeKPIRoleAssignment(
                employee=self.employee,
                kpi_template=self.templates[0],
                role_weight=Decimal("0.00"),
                start_date=self.today,
            ),
            EmployeeKPIRoleAssignment(
                employee=self.employee,
                kpi_template=self.templates[0],
                role_weight=Decimal("100.00"),
                start_date=self.today,
                end_date=self.today - timedelta(days=1),
            ),
            EmployeeKPIRoleAssignment(
                employee=self.employee,
                kpi_template=self.templates[0],
                role_weight=Decimal("100.00"),
                start_date=self.today,
                is_active=True,
                is_archived=True,
            ),
        ]

        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        EmployeeKPIRoleAssignment._base_manager.bulk_create([row])
