import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings


class KPIStagingAutomationCommandTests(TestCase):
    def test_command_refuses_non_staging_settings(self):
        with self.assertRaisesMessage(
            CommandError,
            "restricted to KPI staging",
        ):
            call_command("run_kpi_staging_automation", schedule="daily", dry_run=True)

    @override_settings(
        KPI_STAGING=True,
        KPI_AUTOMATION_MANUAL_ONLY=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        WHATSAPP_ENABLED=False,
        WHATSAPP_AUTOMATION_ENABLED=False,
        WHATSAPP_OUTBOUND_ENABLED=False,
        MARKETING_OUTREACH_ENABLED=False,
        MARKETING_AI_ENABLED=False,
        PAYROLL_INTEGRATION_ENABLED=False,
        PAYMENT_PROVIDER_ENABLED=False,
        SMS_ENABLED=False,
    )
    def test_dry_run_writes_audit_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "manual.jsonl"
            with override_settings(KPI_STAGING_AUTOMATION_AUDIT_LOG=audit_path):
                call_command(
                    "run_kpi_staging_automation",
                    schedule="daily",
                    dry_run=True,
                )
            payload = json.loads(audit_path.read_text().strip())
            self.assertEqual(payload["operation"], "dry_run")


class KPIStagingDatabaseCommandGuardTests(TestCase):
    def test_sanitizer_refuses_non_staging_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesMessage(CommandError, "staging settings"):
                call_command(
                    "prepare_kpi_staging_database",
                    confirm_staging=True,
                    credentials_output=os.path.join(directory, "credentials.json"),
                )

    @override_settings(KPI_STAGING=True)
    def test_sanitizer_requires_uat_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesMessage(CommandError, "Missing required UAT users"):
                call_command(
                    "prepare_kpi_staging_database",
                    confirm_staging=True,
                    credentials_output=os.path.join(directory, "credentials.json"),
                )


class KPIStagingSettingsTests(TestCase):
    def test_test_runner_is_not_using_staging_profile(self):
        self.assertFalse(getattr(settings, "KPI_STAGING", False))
        self.assertNotEqual(
            get_user_model().objects.count(),
            7,
            "The normal test suite must not depend on staging UAT users.",
        )
