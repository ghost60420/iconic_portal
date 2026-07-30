import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from crm.models import AutomationNotification
from crm.models_kpi_notifications import (
    KPIAutomationRun,
    KPINotificationEvent,
    KPI_NOTIFICATION_SCHEDULES,
)
from crm.services.kpi_automation import run_kpi_automation


class Command(BaseCommand):
    help = (
        "Run or clean up CRM-only KPI automation in the private staging "
        "environment. This command refuses to run outside staging."
    )

    def add_arguments(self, parser):
        operation = parser.add_mutually_exclusive_group(required=True)
        operation.add_argument(
            "--schedule",
            choices=sorted(KPI_NOTIFICATION_SCHEDULES),
        )
        operation.add_argument(
            "--cleanup-run",
            type=int,
            metavar="RUN_ID",
        )
        parser.add_argument(
            "--date",
            dest="source_date",
            help="Evaluation date in YYYY-MM-DD format. Defaults to today.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Evaluate inside a transaction and roll back every database write.",
        )

    def handle(self, *args, **options):
        self._assert_staging()
        if options["cleanup_run"]:
            if options["dry_run"]:
                raise CommandError("--dry-run cannot be combined with --cleanup-run.")
            self._cleanup(options["cleanup_run"])
            return

        source_date = timezone.localdate()
        if options.get("source_date"):
            source_date = parse_date(options["source_date"])
            if source_date is None:
                raise CommandError("--date must use YYYY-MM-DD format.")

        if options["dry_run"]:
            with transaction.atomic():
                run = run_kpi_automation(
                    schedule=options["schedule"],
                    as_of=source_date,
                )
                result = self._run_result(run, operation="dry_run")
                transaction.set_rollback(True)
            self._audit(result)
            self.stdout.write(
                self.style.SUCCESS(
                    "Dry run rolled back: "
                    f"{result['created_count']} notification(s) would be created; "
                    f"{result['duplicate_count']} duplicate(s) would be blocked."
                )
            )
            return

        run = run_kpi_automation(
            schedule=options["schedule"],
            as_of=source_date,
        )
        result = self._run_result(run, operation="run")
        self._audit(result)
        if run.status == KPIAutomationRun.STATUS_FAILED:
            raise CommandError(
                f"KPI automation run {run.pk} failed: {run.failure_type}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"KPI automation run {run.pk} completed: "
                f"{run.created_count} created, "
                f"{run.duplicate_count} duplicate(s) blocked."
            )
        )

    @staticmethod
    def _assert_staging():
        if not getattr(settings, "KPI_STAGING", False):
            raise CommandError("This command is restricted to KPI staging.")
        if not getattr(settings, "KPI_AUTOMATION_MANUAL_ONLY", False):
            raise CommandError("Staging manual-only automation is not enabled.")
        if not settings.DATABASES["default"]["NAME"]:
            raise CommandError("The staging database path is missing.")
        if settings.EMAIL_BACKEND != "django.core.mail.backends.locmem.EmailBackend":
            raise CommandError("The staging email backend is not isolated.")
        unsafe_flags = (
            "WHATSAPP_ENABLED",
            "WHATSAPP_AUTOMATION_ENABLED",
            "WHATSAPP_OUTBOUND_ENABLED",
            "MARKETING_OUTREACH_ENABLED",
            "MARKETING_AI_ENABLED",
            "PAYROLL_INTEGRATION_ENABLED",
            "PAYMENT_PROVIDER_ENABLED",
            "SMS_ENABLED",
        )
        if any(getattr(settings, flag, False) for flag in unsafe_flags):
            raise CommandError("An external staging provider flag is enabled.")

    @staticmethod
    def _run_result(run, *, operation):
        return {
            "timestamp": timezone.now().isoformat(),
            "operation": operation,
            "run_id": run.pk,
            "schedule": run.schedule,
            "source_date": run.source_date.isoformat(),
            "status": run.status,
            "candidates_count": run.candidates_count,
            "created_count": run.created_count,
            "duplicate_count": run.duplicate_count,
            "database": str(settings.DATABASES["default"]["NAME"]),
        }

    def _cleanup(self, run_id):
        run = KPIAutomationRun.objects.filter(pk=run_id).first()
        if run is None:
            raise CommandError(f"KPI automation run {run_id} does not exist.")
        events = KPINotificationEvent.service_objects.filter(
            automation_run_id=run_id
        )
        notification_ids = list(
            events.values_list("automation_notification_id", flat=True)
        )
        event_count = len(notification_ids)
        with transaction.atomic():
            events.delete()
            deleted_notifications, _details = AutomationNotification.objects.filter(
                pk__in=notification_ids
            ).delete()
        result = {
            "timestamp": timezone.now().isoformat(),
            "operation": "cleanup",
            "run_id": run_id,
            "event_count": event_count,
            "notification_count": deleted_notifications,
            "database": str(settings.DATABASES["default"]["NAME"]),
        }
        self._audit(result)
        self.stdout.write(
            self.style.SUCCESS(
                f"Cleaned KPI automation run {run_id}: "
                f"{event_count} KPI event(s) and "
                f"{deleted_notifications} CRM notification row(s) removed."
            )
        )

    @staticmethod
    def _audit(payload):
        path = Path(settings.KPI_STAGING_AUTOMATION_AUDIT_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True))
            stream.write("\n")
