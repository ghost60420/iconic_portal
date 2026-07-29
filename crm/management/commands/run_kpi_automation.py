from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date

from crm.models_kpi_notifications import (
    KPIAutomationRun,
    KPI_NOTIFICATION_SCHEDULES,
)
from crm.services.kpi_automation import run_kpi_automation


class Command(BaseCommand):
    help = (
        "Run bounded CRM-only KPI notification automation. "
        "No email, SMS, WhatsApp, or external provider is contacted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schedule",
            required=True,
            choices=sorted(KPI_NOTIFICATION_SCHEDULES),
        )
        parser.add_argument(
            "--date",
            dest="source_date",
            help="Evaluation date in YYYY-MM-DD format. Defaults to today.",
        )

    def handle(self, *args, **options):
        source_date = timezone.localdate()
        if options.get("source_date"):
            source_date = parse_date(options["source_date"])
            if source_date is None:
                raise CommandError("--date must use YYYY-MM-DD format.")
        run = run_kpi_automation(
            schedule=options["schedule"],
            as_of=source_date,
        )
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
