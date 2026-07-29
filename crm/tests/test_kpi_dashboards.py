from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models_kpi_bonus import KPIBonusRuleSet, KPIBonusWeightProfile
from crm.models_kpi_reviews import KPIReview
from crm.services.kpi_assignments import assign_kpi_role
from crm.services.kpi_bonus import create_bonus_calculation
from crm.services.kpi_dashboard import (
    AUDIENCE_DIRECTOR,
    AUDIENCE_EMPLOYEE,
    AUDIENCE_EXECUTIVE,
    AUDIENCE_HR,
    AUDIENCE_MANAGER,
    DashboardFilters,
    dashboard_audience,
    dashboard_filter_options,
    dashboard_widget_payload,
    dashboard_widgets,
)
from crm.services.kpi_reviews import (
    approve_kpi_review,
    create_kpi_review,
    save_kpi_review_draft,
    start_kpi_review,
    submit_kpi_review,
)
from crm.tests.test_kpi_performance_ui import KPIReviewTestBase


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "stage7-dashboard-tests",
        }
    }
)
class KPIDashboardTests(KPIReviewTestBase):
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
            reason="Stage 7 production team assignment",
        )
        cls.employee_user.groups.add(
            Group.objects.get_or_create(name="CA_TEAM")[0]
        )
        cls.other_employee_user.groups.add(
            Group.objects.get_or_create(name="BD_TEAM")[0]
        )

    def setUp(self):
        cache.clear()

    def approved_period(
        self,
        *,
        employee=None,
        manager=None,
        approver=None,
        month=7,
        score=Decimal("90"),
        critical=False,
        period_type=KPIReview.PERIOD_MONTHLY,
    ):
        employee = employee or self.employee
        manager = manager or self.manager
        approver = approver or self.director
        period_start = date(2026, month, 1)
        period_end = date(2026, month, 28)
        review = create_kpi_review(
            employee=employee,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            review_date=date(2026, month, 15),
            actor=manager,
        )
        entry = review.item_entries.get()
        save_kpi_review_draft(
            review,
            actor=manager,
            entry_values={
                entry.pk: {
                    "actual_value": score,
                    "critical_red": critical,
                    "critical_reason": "Critical quality failure" if critical else "",
                    "critical_trigger": "Quality escalation" if critical else "",
                    "item_comment": "Stage 7 approved source",
                }
            },
            manager_comment="Stage 7 review",
        )
        submit_kpi_review(review, actor=manager)
        start_kpi_review(review, actor=approver)
        return approve_kpi_review(
            review,
            actor=approver,
            comment="Approved for Stage 7",
        )

    def create_individual_bonus_rule(self):
        profile = KPIBonusWeightProfile.objects.create(
            code="stage7-individual",
            name="Stage 7 Individual Weights",
            version=1,
            team_scope_code="stage7-team",
            team_scope_name="Stage 7 Team",
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
            code="stage7-individual",
            name="Stage 7 Individual Rules",
            version=1,
            status=KPIBonusRuleSet.STATUS_DRAFT,
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
            approval_required=True,
            effective_start=date(2026, 1, 1),
            created_by=self.admin,
        )
        rule.status = KPIBonusRuleSet.STATUS_PUBLISHED
        rule.published_by = self.admin
        rule.save()
        return rule

    def test_audience_and_widget_sets_are_role_based(self):
        self.assertEqual(dashboard_audience(self.employee_user), AUDIENCE_EMPLOYEE)
        self.assertEqual(dashboard_audience(self.manager), AUDIENCE_MANAGER)
        self.assertEqual(dashboard_audience(self.director), AUDIENCE_DIRECTOR)
        self.assertEqual(dashboard_audience(self.hr), AUDIENCE_HR)
        self.assertEqual(dashboard_audience(self.ceo), AUDIENCE_EXECUTIVE)
        self.assertEqual(dashboard_audience(self.admin), AUDIENCE_EXECUTIVE)

        employee_slugs = {
            widget.slug for widget in dashboard_widgets(self.employee_user)
        }
        executive_slugs = {widget.slug for widget in dashboard_widgets(self.ceo)}
        self.assertIn("employee-score", employee_slugs)
        self.assertNotIn("bonus-forecast", employee_slugs)
        self.assertIn("bonus-forecast", executive_slugs)
        self.assertIn("location-summary", executive_slugs)

    def test_dashboard_requires_login_and_loads_for_employee(self):
        response = self.client.get(reverse("kpi_dashboard"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.employee_user)
        response = self.client.get(reverse("kpi_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "KPI Dashboard")
        self.assertContains(response, "data-widget-slug=\"employee-score\"")
        self.assertNotContains(response, "data-widget-slug=\"bonus-forecast\"")

    def test_employee_widget_uses_only_own_approved_snapshot(self):
        own_review = self.approved_period(score=Decimal("92"))
        other_review = self.approved_period(
            employee=self.other_employee,
            manager=self.other_manager,
            approver=self.other_director,
            score=Decimal("55"),
        )
        self.client.force_login(self.employee_user)
        url = reverse("kpi_dashboard_widget", args=["employee-score"])
        response = self.client.get(
            url,
            {"employee": other_review.employee_id, "year": 2026},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.other_employee.public_name)

        response = self.client.get(
            url,
            {"employee": own_review.employee_id, "year": 2026},
        )
        self.assertContains(response, "92.00")
        self.assertContains(response, self.employee.public_name)

    def test_employee_cannot_open_management_or_bonus_widgets(self):
        self.client.force_login(self.employee_user)
        for slug in ("team-summary", "review-queue", "bonus-forecast"):
            response = self.client.get(
                reverse("kpi_dashboard_widget", args=[slug])
            )
            self.assertEqual(response.status_code, 403)

    def test_manager_sees_only_assigned_team(self):
        self.approved_period(score=Decimal("91"))
        self.approved_period(
            employee=self.other_employee,
            manager=self.other_manager,
            approver=self.other_director,
            score=Decimal("52"),
        )
        payload = dashboard_widget_payload(
            self.manager,
            "leaderboard",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        names = {row["employee"] for row in payload["top"]}
        self.assertEqual(names, {self.employee.public_name})
        self.assertNotIn(self.other_employee.public_name, names)

    def test_director_is_restricted_to_department(self):
        self.approved_period(score=Decimal("89"))
        self.approved_period(
            employee=self.other_employee,
            manager=self.other_manager,
            approver=self.other_director,
            score=Decimal("61"),
        )
        payload = dashboard_widget_payload(
            self.director,
            "department-summary",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        names = {row["name"] for row in payload["rows"]}
        self.assertEqual(names, {"Sales"})

    def test_hr_sees_company_reviews_without_bonus_forecast(self):
        self.approved_period()
        self.approved_period(
            employee=self.other_employee,
            manager=self.other_manager,
            approver=self.other_director,
            score=Decimal("75"),
        )
        summary = dashboard_widget_payload(
            self.hr,
            "team-summary",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        self.assertEqual(summary["employee_count"], 2)
        self.assertNotIn(
            "bonus-forecast",
            {widget.slug for widget in dashboard_widgets(self.hr)},
        )

    def test_ceo_location_filter_uses_configured_team_groups(self):
        self.approved_period(score=Decimal("94"))
        self.approved_period(
            employee=self.other_employee,
            manager=self.other_manager,
            approver=self.other_director,
            score=Decimal("74"),
        )
        payload = dashboard_widget_payload(
            self.ceo,
            "location-summary",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        rows = {row["name"]: row for row in payload["rows"]}
        self.assertEqual(rows["Canada"]["score"], "94.00")
        self.assertEqual(rows["Bangladesh"]["score"], "74.00")

        filtered = dashboard_widget_payload(
            self.ceo,
            "executive-summary",
            DashboardFilters(year=2026, location="bd"),
            use_cache=False,
        )
        self.assertEqual(filtered["score"], "74.00")
        self.assertEqual(filtered["employee_count"], 1)

    def test_filters_cover_employee_role_department_period_manager_and_status(self):
        review = self.approved_period(score=Decimal("88"))
        options = dashboard_filter_options(
            self.ceo,
            DashboardFilters(year=2026),
        )
        self.assertIn(
            self.employee.pk,
            {row["id"] for row in options["employees"]},
        )
        self.assertIn(
            self.template.pk,
            {row["id"] for row in options["roles"]},
        )
        filtered = DashboardFilters(
            employee_id=self.employee.pk,
            role_id=self.template.pk,
            department="sales",
            location="ca",
            period_type=KPIReview.PERIOD_MONTHLY,
            month=7,
            year=2026,
            manager_id=self.manager.pk,
            status="green",
        )
        payload = dashboard_widget_payload(
            self.ceo,
            "executive-summary",
            filtered,
            use_cache=False,
        )
        self.assertEqual(payload["employee_count"], 1)
        self.assertEqual(payload["score"], "88.00")
        self.assertEqual(review.employee_id, self.employee.pk)

    def test_invalid_filters_are_normalized(self):
        filters = DashboardFilters.from_querydict(
            {
                "employee": "-4",
                "month": "99",
                "quarter": "0",
                "year": "not-a-year",
                "status": "approved",
                "location": "private",
                "period": "daily",
            },
            today=date(2026, 7, 1),
        )
        self.assertIsNone(filters.employee_id)
        self.assertIsNone(filters.month)
        self.assertIsNone(filters.quarter)
        self.assertEqual(filters.year, 2026)
        self.assertEqual(filters.status, "")
        self.assertEqual(filters.location, "")
        self.assertEqual(filters.period_type, "")

    def test_trend_uses_stored_snapshots_for_month_quarter_and_year(self):
        self.approved_period(month=6, score=Decimal("80"))
        self.approved_period(month=7, score=Decimal("90"))
        payload = dashboard_widget_payload(
            self.employee_user,
            "trend",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        monthly = {row["label"]: row["value"] for row in payload["series"]["monthly"]}
        quarterly = {
            row["label"]: row["value"]
            for row in payload["series"]["quarterly"]
        }
        self.assertEqual(monthly["Jun"], "80.00")
        self.assertEqual(monthly["Jul"], "90.00")
        self.assertEqual(quarterly["Q2"], "80.00")
        self.assertEqual(quarterly["Q3"], "90.00")
        self.assertEqual(payload["series"]["annual"][0]["value"], "85.00")

    def test_dashboard_never_calls_stage4_engine(self):
        self.approved_period(score=Decimal("93"))
        with patch(
            "crm.services.kpi_calculation_engine.calculate_employee_kpi_score",
            side_effect=AssertionError("dashboard recalculated KPI"),
        ):
            payload = dashboard_widget_payload(
                self.employee_user,
                "employee-score",
                DashboardFilters(year=2026),
                use_cache=False,
            )
        self.assertEqual(payload["score"], "93.00")
        self.assertTrue(payload["snapshot_only"])

    def test_critical_red_and_improvement_widgets(self):
        self.approved_period(score=Decimal("95"), critical=True)
        risk = dashboard_widget_payload(
            self.ceo,
            "risk",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        self.assertEqual(len(risk["rows"]), 1)
        self.assertTrue(risk["rows"][0]["critical"])
        improvement = dashboard_widget_payload(
            self.employee_user,
            "improvement",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        self.assertEqual(improvement["rows"][0]["status"], "red")

    def test_bonus_eligibility_and_executive_forecast_use_stage6_snapshot(self):
        review = self.approved_period(score=Decimal("90"))
        rule = self.create_individual_bonus_rule()
        create_bonus_calculation(
            review,
            rule_set=rule,
            base_bonus_amount=Decimal("500"),
            actor=self.admin,
        )
        employee = dashboard_widget_payload(
            self.employee_user,
            "employee-score",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        forecast = dashboard_widget_payload(
            self.ceo,
            "bonus-forecast",
            DashboardFilters(year=2026),
            use_cache=False,
        )
        self.assertEqual(employee["bonus_eligibility"], "Eligible")
        self.assertNotIn("final_bonus_amount", employee)
        self.assertEqual(forecast["currency_totals"][0]["currency"], "CAD")
        self.assertEqual(forecast["currency_totals"][0]["amount"], "450.00")

    def test_every_executive_widget_renders_independently(self):
        self.approved_period(score=Decimal("91"))
        self.client.force_login(self.ceo)
        for widget in dashboard_widgets(self.ceo):
            response = self.client.get(
                reverse("kpi_dashboard_widget", args=[widget.slug]),
                {"year": 2026},
            )
            self.assertEqual(response.status_code, 200, widget.slug)
            self.assertEqual(
                response.headers["X-KPI-Source"],
                "approved-snapshots",
            )
            self.assertContains(response, widget.title)

    def test_chart_widget_renders_canvas_data(self):
        self.approved_period(score=Decimal("87"))
        self.client.force_login(self.employee_user)
        response = self.client.get(
            reverse("kpi_dashboard_widget", args=["trend"]),
            {"year": 2026},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-kpi-chart", count=3)
        self.assertContains(response, "Monthly")
        self.assertContains(response, "Quarterly")
        self.assertContains(response, "Annual")

    def test_widget_cache_is_user_and_filter_scoped(self):
        self.approved_period(score=Decimal("86"))
        filters = DashboardFilters(year=2026)
        dashboard_audience(self.ceo)
        with CaptureQueriesContext(connection) as first_queries:
            first = dashboard_widget_payload(
                self.ceo,
                "executive-summary",
                filters,
            )
        with CaptureQueriesContext(connection) as cached_queries:
            second = dashboard_widget_payload(
                self.ceo,
                "executive-summary",
                filters,
            )
        self.assertEqual(first, second)
        self.assertGreater(len(first_queries), 0)
        self.assertEqual(len(cached_queries), 0)

        different = dashboard_widget_payload(
            self.ceo,
            "executive-summary",
            DashboardFilters(year=2026, status="red"),
        )
        self.assertNotEqual(first["employee_count"], different["employee_count"])

    def test_employee_widget_stays_within_query_budget(self):
        self.approved_period(score=Decimal("90"))
        dashboard_audience(self.employee_user)
        with CaptureQueriesContext(connection) as queries:
            payload = dashboard_widget_payload(
                self.employee_user,
                "employee-score",
                DashboardFilters(year=2026),
                use_cache=False,
            )
        self.assertEqual(payload["score"], "90.00")
        self.assertLessEqual(
            len(queries),
            5,
            [query["sql"] for query in queries],
        )

    def test_dashboard_shell_stays_within_query_budget(self):
        self.client.force_login(self.employee_user)
        self.client.get(reverse("kpi_dashboard"), {"year": 2026})
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("kpi_dashboard"), {"year": 2026})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(queries),
            10,
            [query["sql"] for query in queries],
        )

    def test_export_capabilities_are_authenticated_and_generation_is_disabled(self):
        url = reverse("kpi_dashboard_export_capabilities")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.ceo)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["generation_enabled"])
        self.assertEqual(
            {row["format"] for row in payload["capabilities"]},
            {"pdf", "xlsx", "csv", "print"},
        )
        self.assertFalse(any(row["available"] for row in payload["capabilities"]))

    def test_dashboard_routes_are_read_only(self):
        self.client.force_login(self.ceo)
        self.assertEqual(self.client.post(reverse("kpi_dashboard")).status_code, 405)
        self.assertEqual(
            self.client.post(
                reverse("kpi_dashboard_widget", args=["executive-summary"])
            ).status_code,
            405,
        )

    def test_mobile_and_lazy_loading_assets_are_present(self):
        self.client.force_login(self.employee_user)
        response = self.client.get(reverse("kpi_dashboard"))
        self.assertContains(response, "data-kpi-widget")
        self.assertContains(response, "kpi_dashboard.js")
        self.assertContains(response, "kpi_dashboard.css")
