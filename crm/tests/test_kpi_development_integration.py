from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from crm.models_employee import EmployeeProfile
from crm.models_kpi import (
    KPIItemDefinition,
    KPIRoleTemplate,
    KPISettings,
    KPITemplateVersion,
)
from crm.models_kpi_assignments import EmployeeKPIRoleAssignment
from crm.models_kpi_release import KPIPolicyApproval
from crm.services.kpi_release import create_policy_approval


class KPIDevelopmentIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            "kpi-integration-admin",
            "admin@example.com",
            "password",
        )
        cls.manager = user_model.objects.create_user(
            "kpi-integration-manager",
            first_name="KPI",
            last_name="Manager",
            password="password",
        )
        cls.employee_user = user_model.objects.create_user(
            "kpi-integration-employee",
            first_name="KPI",
            last_name="Employee",
            password="password",
        )
        cls.manager.groups.add(Group.objects.get_or_create(name="Manager")[0])
        manager_profile = EmployeeProfile.objects.get(user=cls.manager)
        manager_profile.department = "sales"
        manager_profile.save(update_fields=("department",))
        cls.employee = EmployeeProfile.objects.get(user=cls.employee_user)
        cls.employee.department = "sales"
        cls.employee.save(update_fields=("department",))

        cls.template = KPIRoleTemplate.objects.create(
            code="integration-sales",
            name="Integration Sales",
            created_by=cls.admin,
        )
        cls.version = KPITemplateVersion.objects.create(
            template=cls.template,
            version=1,
            effective_start=date(2026, 1, 1),
            created_by=cls.admin,
        )
        KPIItemDefinition.objects.create(
            template_version=cls.version,
            name="Integration score",
            measurement_method="Manual score",
            target="100",
            weight=Decimal("100.00"),
            data_source="Development integration test",
        )
        cls.version.status = KPITemplateVersion.STATUS_PUBLISHED
        cls.version.published_by = cls.admin
        cls.version.save()

        settings_policy = KPISettings.objects.create(
            version=1,
            created_by=cls.admin,
        )
        cls.policy = create_policy_approval(settings_policy, actor=cls.admin)

    def test_setup_routes_require_executive_server_permission(self):
        self.client.force_login(self.employee_user)

        assignments = self.client.get(reverse("kpi_assignment_management"))
        policies = self.client.get(reverse("kpi_policy_management"))

        self.assertEqual(assignments.status_code, 403)
        self.assertEqual(policies.status_code, 403)

    def test_executive_can_open_setup_pages(self):
        self.client.force_login(self.admin)

        assignments = self.client.get(reverse("kpi_assignment_management"))
        policies = self.client.get(reverse("kpi_policy_management"))

        self.assertEqual(assignments.status_code, 200)
        self.assertContains(assignments, "KPI Assignments")
        self.assertContains(assignments, "Role 3")
        self.assertEqual(policies.status_code, 200)
        self.assertContains(policies, "KPI Policies")
        self.assertContains(policies, "KPI Status Settings")

    def test_assignment_page_delegates_exact_weight_validation_to_service(self):
        self.client.force_login(self.admin)
        url = reverse("kpi_assignment_management")
        response = self.client.post(
            url,
            {
                "action": "save_draft",
                "employee": self.employee.pk,
                "start_date": "2026-01-01",
                "role_1_template": self.template.pk,
                "role_1_weight": "90",
                "role_1_manager": self.manager.pk,
                "role_1_bonus": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must total exactly 100.00")
        self.assertFalse(
            EmployeeKPIRoleAssignment.objects.filter(employee=self.employee).exists()
        )

    def test_assignment_page_saves_and_activates_service_validated_draft(self):
        self.client.force_login(self.admin)
        url = reverse("kpi_assignment_management")
        saved = self.client.post(
            url,
            {
                "action": "save_draft",
                "employee": self.employee.pk,
                "start_date": "2026-01-01",
                "role_1_template": self.template.pk,
                "role_1_weight": "100",
                "role_1_manager": self.manager.pk,
                "role_1_bonus": "on",
                "role_1_notes": "Development test assignment",
            },
        )

        self.assertEqual(saved.status_code, 302)
        draft = EmployeeKPIRoleAssignment.objects.get(employee=self.employee)
        self.assertFalse(draft.is_active)

        activated = self.client.post(
            url,
            {
                "action": "activate",
                "employee": self.employee.pk,
                "assignment_ids": [draft.pk],
                "reason": "Approved development test activation",
            },
        )

        self.assertEqual(activated.status_code, 302)
        draft.refresh_from_db()
        self.assertTrue(draft.is_active)
        self.assertEqual(draft.role_weight, Decimal("100.00"))

    def test_policy_page_uses_controlled_transition_service(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("kpi_policy_management"),
            {
                "policy_id": self.policy.pk,
                "action": "submit",
                "reason": "Development integration review",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.policy.refresh_from_db()
        self.assertEqual(
            self.policy.status,
            KPIPolicyApproval.STATUS_UNDER_REVIEW,
        )

    def test_navigation_hides_setup_from_employee_and_shows_it_to_executive(self):
        self.client.force_login(self.employee_user)
        employee_response = self.client.get(reverse("kpi_dashboard"))
        self.assertEqual(employee_response.status_code, 200)
        self.assertContains(employee_response, "KPI &amp; Performance")
        self.assertNotContains(employee_response, "KPI Assignments")
        self.assertNotContains(employee_response, "KPI Policies")

        self.client.force_login(self.admin)
        executive_response = self.client.get(reverse("kpi_dashboard"))
        self.assertEqual(executive_response.status_code, 200)
        self.assertContains(executive_response, "KPI Assignments")
        self.assertContains(executive_response, "KPI Policies")

    def test_development_settings_disable_external_delivery(self):
        self.assertEqual(
            settings.DEVELOPMENT_DATABASE_PATH.name,
            "kpi_development.sqlite3",
        )
        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
        )
        self.assertEqual(settings.EMAIL_SYNC, {})
        self.assertEqual(settings.EMAIL_MONITOR, {})
        self.assertTrue(settings.MARKETING_ENABLED)
        self.assertFalse(settings.MARKETING_SEO_ENABLED)
        self.assertFalse(settings.MARKETING_SOCIAL_ENABLED)
        self.assertFalse(settings.MARKETING_OUTREACH_ENABLED)
        self.assertFalse(settings.MARKETING_ADS_ENABLED)
        self.assertFalse(settings.MARKETING_AI_ENABLED)
        self.assertEqual(settings.MARKETING_GOOGLE_CLIENT_SECRET, "")
        self.assertEqual(settings.MARKETING_META_APP_SECRET, "")
        self.assertEqual(settings.MARKETING_LINKEDIN_CLIENT_SECRET, "")
        self.assertEqual(settings.MARKETING_TIKTOK_CLIENT_SECRET, "")
        self.assertEqual(settings.OPENAI_API_KEY, "")
        self.assertFalse(settings.WHATSAPP_ENABLED)
        self.assertFalse(settings.WHATSAPP_AUTOMATION_ENABLED)
        self.assertFalse(settings.WHATSAPP_OUTBOUND_ENABLED)
        self.assertFalse(settings.WA_AUTO_REPLY_ENABLED)
        self.assertEqual(settings.WHATSAPP_SERVICE_URL, "")
        self.assertEqual(settings.WA_TOKEN, "")
        self.assertFalse(settings.CELERY_TASK_ALWAYS_EAGER)
        self.assertEqual(settings.CELERY_BROKER_URL, "memory://")
        self.assertEqual(settings.INVOICE_PAYPAL_EMAIL, "")
