from django.core.management.base import BaseCommand

from crm.services.opportunity_stage_audit import build_repair_command_preview


class Command(BaseCommand):
    help = "Dry-run only: preview invoice link repairs from the CRM integrity audit."

    WARNING_CODES = {"invoice_link_missing", "invoice_link_conflict"}

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Default and only supported mode. No records are modified.",
        )

    def handle(self, *args, **options):
        preview = build_repair_command_preview(
            "repair_invoice_links",
            filter_codes=self.WARNING_CODES,
        )
        self.stdout.write("DRY RUN ONLY - no records were modified.")
        self.stdout.write(f"Candidate invoice link repairs: {preview['count']}")
        for record in preview["records"]:
            self.stdout.write(
                "Invoice {invoice_id}: {recommended_repair_action}".format(**record)
            )
