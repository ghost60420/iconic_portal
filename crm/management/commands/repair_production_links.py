from django.core.management.base import BaseCommand

from crm.services.opportunity_stage_audit import build_repair_command_preview


class Command(BaseCommand):
    help = "Dry-run only: preview production link repairs from the CRM integrity audit."

    WARNING_CODES = {
        "production_link_missing",
        "production_stage_incorrect",
        "duplicate_production_links",
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Default and only supported mode. No records are modified.",
        )

    def handle(self, *args, **options):
        preview = build_repair_command_preview(
            "repair_production_links",
            filter_codes=self.WARNING_CODES,
        )
        self.stdout.write("DRY RUN ONLY - no records were modified.")
        self.stdout.write(f"Candidate production link repairs: {preview['count']}")
        for record in preview["records"]:
            self.stdout.write(
                "#{opportunity_id} {opportunity_number}: {recommended_repair_action}".format(**record)
            )
