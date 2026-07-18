from django.core.management.base import BaseCommand

from crm.services.opportunity_stage_audit import build_repair_command_preview


class Command(BaseCommand):
    help = "Dry-run only: preview opportunity stage repairs from the CRM integrity audit."

    WARNING_CODES = {
        "invoice_stage_incorrect",
        "awaiting_payment_invalid",
        "production_stage_incorrect",
        "completed_stage_incorrect",
        "proposal_has_downstream_records",
        "negotiation_has_invoice",
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
            "repair_opportunity_stages",
            filter_codes=self.WARNING_CODES,
        )
        self.stdout.write("DRY RUN ONLY - no records were modified.")
        self.stdout.write(f"Candidate opportunity stage repairs: {preview['count']}")
        for record in preview["records"]:
            self.stdout.write(
                "#{opportunity_id} {opportunity_number}: {recommended_repair_action}".format(**record)
            )
