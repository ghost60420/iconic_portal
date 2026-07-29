from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models import (
    CRMAuditLog,
    EmployeeProfile,
    KPIItemDefinition,
    KPIRoleTemplate,
    KPISettings,
    KPITemplateVersion,
)
from crm.models_kpi_reviews import (
    KPIReview,
    KPIReviewItemEntry,
    KPIReviewTransition,
)
from crm.services.kpi_assignments import assign_kpi_role
from crm.services.kpi_calculation_engine import (
    CALCULATION_ENGINE_VERSION,
    FORMULA_VERSION,
)
from crm.services.kpi_review_permissions import (
    can_approve_kpi_review,
    can_manage_kpi_review,
    can_view_kpi_review,
)
from crm.services.operations_permissions import (
    employee_department,
    operations_role_names,
)
from crm.services.kpi_reviews import (
    KPIReviewWorkflowError,
    approve_kpi_review,
    create_kpi_review,
    lock_kpi_review,
    reject_kpi_review,
    save_kpi_review_draft,
    start_kpi_review,
    submit_kpi_review,
    verify_approved_snapshot,
)


class KPIReviewTestBase(TestCase):
    review_date = date(2026, 7, 15)
    period_start = date(2026, 7, 1)
    period_end = date(2026, 7, 31)

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            "stage5-admin",
            "stage5-admin@example.com",
            "password",
        )
        cls.employee_user = user_model.objects.create_user(
            "stage5-employee",
            first_name="KPI",
            last_name="Employee",
            password="password",
        )
        cls.other_employee_user = user_model.objects.create_user(
            "stage5-other-employee",
            first_name="Other",
            last_name="Employee",
            password="password",
        )
        cls.manager = user_model.objects.create_user(
            "stage5-manager",
            first_name="KPI",
            last_name="Manager",
            password="password",
        )
        cls.other_manager = user_model.objects.create_user(
            "stage5-other-manager",
            first_name="Other",
            last_name="Manager",
            password="password",
        )
        cls.director = user_model.objects.create_user(
            "stage5-director",
            first_name="KPI",
            last_name="Director",
            password="password",
        )
        cls.other_director = user_model.objects.create_user(
            "stage5-other-director",
            first_name="Other",
            last_name="Director",
            password="password",
        )
        cls.hr = user_model.objects.create_user(
            "stage5-hr",
            first_name="KPI",
            last_name="HR",
            password="password",
        )
        cls.ceo = user_model.objects.create_user(
            "stage5-ceo",
            first_name="KPI",
            last_name="CEO",
            password="password",
        )

        for user, role in (
            (cls.manager, "Manager"),
            (cls.other_manager, "Manager"),
            (cls.director, "Director"),
            (cls.other_director, "Director"),
            (cls.hr, "HR"),
            (cls.ceo, "CEO"),
        ):
            user.groups.add(Group.objects.get_or_create(name=role)[0])

        cls.employee = EmployeeProfile.objects.get(user=cls.employee_user)
        cls.other_employee = EmployeeProfile.objects.get(
            user=cls.other_employee_user
        )
        for profile in (
            cls.employee,
            EmployeeProfile.objects.get(user=cls.manager),
            EmployeeProfile.objects.get(user=cls.director),
            EmployeeProfile.objects.get(user=cls.hr),
            EmployeeProfile.objects.get(user=cls.ceo),
        ):
            profile.department = "sales"
            profile.save(update_fields=["department"])
        for profile in (
            cls.other_employee,
            EmployeeProfile.objects.get(user=cls.other_manager),
            EmployeeProfile.objects.get(user=cls.other_director),
        ):
            profile.department = "production"
            profile.save(update_fields=["department"])
        for user in (
            cls.employee_user,
            cls.other_employee_user,
            cls.manager,
            cls.other_manager,
            cls.director,
            cls.other_director,
            cls.hr,
            cls.ceo,
        ):
            user.refresh_from_db()

        KPISettings.objects.create(
            version=1,
            is_active=True,
            created_by=cls.admin,
        )
        cls.template = KPIRoleTemplate.objects.create(
            code="stage5-sales",
            name="Stage 5 Sales",
            created_by=cls.admin,
        )
        cls.template_version = KPITemplateVersion.objects.create(
            template=cls.template,
            version=3,
            effective_start=date(2026, 1, 1),
            created_by=cls.admin,
        )
        cls.item = KPIItemDefinition.objects.create(
            template_version=cls.template_version,
            name="Stage 5 Manual KPI",
            measurement_method="Manual score",
            target="100",
            weight=Decimal("100.00"),
            data_source="Stage 5 tests",
        )
        cls.template_version.status = KPITemplateVersion.STATUS_PUBLISHED
        cls.template_version.published_by = cls.admin
        cls.template_version.save()
        cls.assignment = assign_kpi_role(
            employee=cls.employee,
            kpi_template=cls.template,
            role_weight=Decimal("100.00"),
            manager=cls.manager,
            start_date=date(2026, 1, 1),
            actor=cls.admin,
            reason="Stage 5 test assignment",
        )

    def create_review(self, *, actor=None):
        return create_kpi_review(
            employee=self.employee,
            period_type=KPIReview.PERIOD_MONTHLY,
            period_start=self.period_start,
            period_end=self.period_end,
            review_date=self.review_date,
            actor=actor or self.manager,
        )

    def save_review(self, review, score=Decimal("90"), *, critical=False):
        entry = review.item_entries.get()
        return save_kpi_review_draft(
            review,
            actor=self.manager,
            entry_values={
                entry.pk: {
                    "actual_value": score,
                    "critical_red": critical,
                    "critical_reason": "Material quality failure" if critical else "",
                    "critical_trigger": "QC escalation" if critical else "",
                    "item_comment": "Measured result",
                }
            },
            manager_comment="Manager review comment",
        )

    def approved_review(self, score=Decimal("90"), *, critical=False):
        review = self.create_review()
        self.save_review(review, score, critical=critical)
        submit_kpi_review(review, actor=self.manager)
        start_kpi_review(review, actor=self.director)
        return approve_kpi_review(
            review,
            actor=self.director,
            comment="Approved after review",
        )


class KPIReviewWorkflowTests(KPIReviewTestBase):
    def test_review_creation_freezes_assignments_templates_and_entries(self):
        review = self.create_review()

        self.assertEqual(review.status, KPIReview.STATUS_DRAFT)
        self.assertEqual(review.manager, self.manager)
        self.assertEqual(review.item_entries.count(), 1)
        role = review.definition_snapshot["roles"][0]
        self.assertEqual(role["assignment_version"], 1)
        self.assertEqual(role["template_version"], 3)
        self.assertEqual(role["role_weight"], "100.00")
        self.assertEqual(
            review.transitions.get().action,
            KPIReviewTransition.ACTION_CREATED,
        )

    def test_draft_save_uses_stage4_engine_and_preserves_versions(self):
        review = self.create_review()
        review = self.save_review(review, Decimal("88.25"))

        self.assertEqual(review.calculation_snapshot["score"], "88.250000")
        self.assertEqual(
            review.calculation_snapshot["calculation_version"],
            CALCULATION_ENGINE_VERSION,
        )
        self.assertEqual(
            review.calculation_snapshot["formula_version"],
            FORMULA_VERSION,
        )
        self.assertEqual(
            review.calculation_snapshot["template_versions"][0]["version"],
            3,
        )
        self.assertEqual(
            review.calculation_snapshot["assignment_versions"][0]["version"],
            1,
        )

    def test_submit_start_and_approve_require_ordered_workflow(self):
        review = self.create_review()
        self.save_review(review)

        with self.assertRaisesMessage(
            KPIReviewWorkflowError,
            "Only reviews Under Review",
        ):
            approve_kpi_review(review, actor=self.director)

        review = submit_kpi_review(review, actor=self.manager)
        self.assertEqual(review.status, KPIReview.STATUS_SUBMITTED)
        review = start_kpi_review(review, actor=self.director)
        self.assertEqual(review.status, KPIReview.STATUS_UNDER_REVIEW)
        review = approve_kpi_review(review, actor=self.director)
        self.assertEqual(review.status, KPIReview.STATUS_APPROVED)

    def test_manager_cannot_approve_and_employee_cannot_self_approve(self):
        review = self.create_review()
        self.save_review(review)
        submit_kpi_review(review, actor=self.manager)

        self.assertFalse(can_approve_kpi_review(self.manager, review))
        with self.assertRaisesMessage(
            KPIReviewWorkflowError,
            "permission",
        ):
            start_kpi_review(review, actor=self.manager)

        review.employee = EmployeeProfile.objects.get(user=self.director)
        self.assertFalse(can_approve_kpi_review(self.director, review))

    def test_rejection_returns_review_to_draft_and_preserves_history(self):
        review = self.create_review()
        self.save_review(review)
        submit_kpi_review(review, actor=self.manager)
        start_kpi_review(review, actor=self.director)

        review = reject_kpi_review(
            review,
            actor=self.director,
            comment="Correct the source value.",
        )

        self.assertEqual(review.status, KPIReview.STATUS_DRAFT)
        self.assertEqual(review.rejection_comment, "Correct the source value.")
        self.assertEqual(
            list(
                review.transitions.order_by("id").values_list(
                    "action", flat=True
                )
            )[-2:],
            [
                KPIReviewTransition.ACTION_REJECTED,
                KPIReviewTransition.ACTION_RETURNED_TO_DRAFT,
            ],
        )

    def test_approval_snapshot_is_complete_and_digest_is_valid(self):
        review = self.approved_review()

        self.assertTrue(verify_approved_snapshot(review))
        self.assertEqual(review.approved_snapshot["result"]["score"], "90.000000")
        self.assertEqual(
            review.approved_snapshot["definition"]["roles"][0][
                "template_version"
            ],
            3,
        )
        self.assertEqual(
            review.approved_snapshot["entries"][0]["actual_value"],
            "90.000000",
        )
        self.assertEqual(
            review.approved_snapshot["approval"]["approved_by_id"],
            self.director.pk,
        )
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="kpi_performance",
                record_id=str(review.pk),
                action_type=CRMAuditLog.ACTION_APPROVED,
            ).exists()
        )

    def test_critical_red_keeps_score_and_overrides_status(self):
        review = self.approved_review(Decimal("95"), critical=True)

        result = review.result_snapshot
        self.assertEqual(result["score"], "95.000000")
        self.assertEqual(result["status"], "red")
        self.assertTrue(result["critical_red"])

    def test_approved_review_and_entries_are_read_only(self):
        review = self.approved_review()
        original_snapshot = review.approved_snapshot
        review.manager_comment = "Changed outside workflow"
        with self.assertRaisesMessage(ValidationError, "read only"):
            review.save()

        entry = review.item_entries.get()
        entry.actual_value = Decimal("10")
        with self.assertRaisesMessage(ValidationError, "Only Draft"):
            entry.save(service_authorized=True)

        review.refresh_from_db()
        self.assertEqual(review.approved_snapshot, original_snapshot)

    def test_direct_draft_entry_edit_is_blocked_outside_service(self):
        review = self.create_review()
        entry = review.item_entries.get()
        entry.actual_value = Decimal("80")

        with self.assertRaisesMessage(ValidationError, "review service"):
            entry.save()

    def test_review_history_creation_is_service_only(self):
        review = self.create_review()

        with self.assertRaisesMessage(ValidationError, "review service"):
            KPIReviewTransition(
                review=review,
                action=KPIReviewTransition.ACTION_SAVED,
                from_status=KPIReview.STATUS_DRAFT,
                to_status=KPIReview.STATUS_DRAFT,
                actor=self.manager,
            ).save()

    def test_locked_review_remains_immutable(self):
        review = self.approved_review()
        review = lock_kpi_review(review, actor=self.director)
        self.assertEqual(review.status, KPIReview.STATUS_LOCKED)
        self.assertTrue(verify_approved_snapshot(review))

        review.approval_comment = "Changed"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            review.save()

    def test_historical_result_does_not_recalculate_on_read(self):
        review = self.approved_review(Decimal("87.5"))
        frozen = review.approved_snapshot

        settings = KPISettings.objects.get(is_active=True)
        settings.is_active = False
        settings.save()
        KPISettings.objects.create(
            version=2,
            is_active=True,
            red_max=Decimal("59.99"),
            yellow_min=Decimal("60.00"),
            yellow_max=Decimal("79.99"),
            green_min=Decimal("80.00"),
            created_by=self.admin,
        )

        review.refresh_from_db()
        self.assertEqual(review.approved_snapshot, frozen)
        self.assertEqual(review.result_snapshot["score"], "87.500000")
        self.assertTrue(verify_approved_snapshot(review))


class KPIReviewPermissionAndUITests(KPIReviewTestBase):
    def test_employee_can_view_own_performance_but_not_another_employee(self):
        self.client.force_login(self.employee_user)

        own = self.client.get(
            reverse("employee_performance", args=[self.employee_user.pk])
        )
        other = self.client.get(
            reverse("employee_performance", args=[self.other_employee_user.pk])
        )

        self.assertEqual(own.status_code, 200)
        self.assertContains(own, "Performance")
        self.assertEqual(other.status_code, 403)

    def test_employee_only_sees_approved_review_detail(self):
        review = self.create_review()
        self.client.force_login(self.employee_user)
        self.assertEqual(
            self.client.get(
                reverse("kpi_review_detail", args=[review.pk])
            ).status_code,
            403,
        )

        self.save_review(review)
        submit_kpi_review(review, actor=self.manager)
        start_kpi_review(review, actor=self.director)
        approve_kpi_review(review, actor=self.director)

        response = self.client.get(
            reverse("kpi_review_detail", args=[review.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Read only")
        self.assertNotContains(response, "Save Draft")

    def test_assigned_manager_can_view_and_edit_but_other_manager_cannot(self):
        review = self.create_review()
        self.assertTrue(can_manage_kpi_review(self.manager, review))
        self.assertFalse(can_manage_kpi_review(self.other_manager, review))

        self.client.force_login(self.manager)
        allowed = self.client.get(
            reverse("kpi_review_detail", args=[review.pk])
        )
        self.client.force_login(self.other_manager)
        denied = self.client.get(
            reverse("kpi_review_detail", args=[review.pk])
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertContains(allowed, "Save Draft")
        self.assertEqual(denied.status_code, 403)

    def test_director_is_scoped_to_department(self):
        review = self.create_review()
        self.assertTrue(
            can_view_kpi_review(self.director, review),
            (
                operations_role_names(self.director),
                employee_department(self.director),
                review.employee.department,
            ),
        )
        self.assertFalse(can_view_kpi_review(self.other_director, review))

    def test_hr_can_view_all_but_cannot_manage_or_approve(self):
        review = self.create_review()
        self.assertTrue(can_view_kpi_review(self.hr, review))
        self.assertFalse(can_manage_kpi_review(self.hr, review))
        self.assertFalse(can_approve_kpi_review(self.hr, review))

        self.client.force_login(self.hr)
        response = self.client.get(
            reverse("kpi_review_detail", args=[review.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Save Draft")
        self.assertEqual(
            self.client.post(
                reverse("kpi_review_submit", args=[review.pk])
            ).status_code,
            403,
        )

    def test_ceo_and_super_admin_have_full_non_self_access(self):
        review = self.create_review()
        self.assertTrue(can_view_kpi_review(self.ceo, review))
        self.assertTrue(can_manage_kpi_review(self.ceo, review))
        self.assertTrue(can_approve_kpi_review(self.ceo, review))
        self.assertTrue(can_view_kpi_review(self.admin, review))

    def test_profile_contains_performance_tab_without_moving_profile_tab(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("employee_edit", args=[self.employee_user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">Profile<")
        self.assertContains(response, ">Performance<")

    def test_post_workflow_enforces_submit_review_approve_lock_sequence(self):
        review = self.create_review()
        entry = review.item_entries.get()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("kpi_review_save", args=[review.pk]),
            {
                f"actual_{entry.pk}": "92",
                f"comment_{entry.pk}": "Completed",
                "manager_comment": "Ready",
                "intent": "submit",
            },
        )
        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, KPIReview.STATUS_SUBMITTED)

        self.client.force_login(self.director)
        self.assertEqual(
            self.client.post(
                reverse("kpi_review_start", args=[review.pk])
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                reverse("kpi_review_approve", args=[review.pk]),
                {"comment": "Approved"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                reverse("kpi_review_lock", args=[review.pk])
            ).status_code,
            302,
        )
        review.refresh_from_db()
        self.assertEqual(review.status, KPIReview.STATUS_LOCKED)

    def test_no_review_routes_are_public(self):
        review = self.create_review()
        urls = (
            reverse("employee_performance", args=[self.employee_user.pk]),
            reverse("kpi_review_list"),
            reverse("kpi_review_detail", args=[review.pk]),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.url)

    def test_performance_page_uses_stored_snapshot(self):
        review = self.approved_review(Decimal("91"))
        self.client.force_login(self.employee_user)

        response = self.client.get(
            reverse("employee_performance", args=[self.employee_user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "91.00")
        self.assertContains(response, CALCULATION_ENGINE_VERSION)
        self.assertContains(response, "Stage 5 Sales")
        self.assertEqual(response.context["latest_completed"].pk, review.pk)

    def test_approved_detail_uses_frozen_definition_and_entry_snapshot(self):
        review = self.approved_review(Decimal("91"))
        self.template.name = "Renamed Live Template"
        self.template.save(update_fields=["name", "updated_at"])
        self.client.force_login(self.employee_user)

        response = self.client.get(
            reverse("kpi_review_detail", args=[review.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stage 5 Sales")
        self.assertContains(response, "91.00")
        self.assertNotContains(response, "Renamed Live Template")

    def test_employee_performance_page_has_bounded_warm_queries(self):
        self.approved_review()
        self.client.force_login(self.employee_user)
        url = reverse("employee_performance", args=[self.employee_user.pk])
        self.client.get(url)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(queries),
            8,
            [query["sql"] for query in queries],
        )
        self.assertIn("kpi-performance;dur=", response.headers["Server-Timing"])

    def test_review_detail_page_has_bounded_warm_queries(self):
        review = self.approved_review()
        self.client.force_login(self.director)
        url = reverse("kpi_review_detail", args=[review.pk])
        self.client.get(url)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(queries),
            8,
            [query["sql"] for query in queries],
        )
        self.assertIn("kpi-review-detail;dur=", response.headers["Server-Timing"])

    def test_review_queue_has_bounded_warm_queries(self):
        self.create_review()
        self.client.force_login(self.manager)
        url = reverse("kpi_review_list")
        self.client.get(url)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(queries),
            10,
            [query["sql"] for query in queries],
        )
        self.assertIn("kpi-review-list;dur=", response.headers["Server-Timing"])
