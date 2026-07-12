from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(OPENAI_API_KEY="")
class ControlCenterUITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="control-center-ui-admin",
            email="control-center-ui-admin@example.com",
            password="pass",
        )
        self.client.force_login(self.user)

    def test_ceo_dashboard_uses_control_center_shell_and_preserves_filters(self):
        response = self.client.get(reverse("ceo_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "crm-modern-bridge")
        self.assertContains(response, "crm-dashboard-bridge")
        self.assertContains(response, "CEO Executive Dashboard")
        self.assertContains(response, reverse("ceo_operations_dashboard"))
        self.assertContains(response, reverse("executive_financial_dashboard"))
        self.assertContains(response, reverse("ceo_quotation_approval_queue"))
        self.assertContains(response, "Total Active Revisions")
        self.assertContains(response, "Superseded Revisions")
        self.assertContains(response, "Recalled Revisions")

    def test_ai_operations_preserves_question_form_and_links(self):
        response = self.client.get(reverse("ai_operations_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "control-modern")
        self.assertContains(response, "control-alerts")
        self.assertContains(response, 'name="question"')
        self.assertContains(response, "Ask")
        self.assertContains(response, reverse("ai_operations_assistant"))
        self.assertContains(response, reverse("ai_health_monitor"))
        self.assertContains(response, reverse("ai_system_status"))

    def test_ai_health_monitor_uses_mobile_tables_without_losing_log_data(self):
        response = self.client.get(reverse("ai_health_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "control-modern")
        self.assertContains(response, "AI Health Monitor")
        self.assertContains(response, "Health Checks")
        self.assertContains(response, "Error History")
        self.assertContains(response, "crm-table-mobile-cards")
        self.assertContains(response, 'href="#control-logs"')

    def test_ai_system_status_uses_control_center_shell(self):
        response = self.client.get(reverse("ai_system_status"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "control-modern")
        self.assertContains(response, "AI System Status")
        self.assertContains(response, "CRM Quick Signals")
        self.assertContains(response, "Error History")
        self.assertContains(response, "crm-table-mobile-cards")
        self.assertContains(response, reverse("ai_health_monitor"))
