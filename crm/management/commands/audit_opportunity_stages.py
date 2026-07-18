from pathlib import Path

from django.core.management.base import BaseCommand

from crm.services.opportunity_stage_audit import (
    build_opportunity_stage_audit,
    sync_opportunity_stage_audit_notification,
    write_crm_data_integrity_details,
    write_crm_integrity_csv,
    write_opportunity_stage_audit_report,
)


class Command(BaseCommand):
    help = "Generate the read-only Opportunity stage integrity audit report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="OPPORTUNITY_STAGE_AUDIT_REPORT.md",
            help="Report output path. Defaults to OPPORTUNITY_STAGE_AUDIT_REPORT.md in the current directory.",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Create or resolve the CEO summary notification based on the audit result.",
        )
        parser.add_argument(
            "--details-output",
            default="CRM_DATA_INTEGRITY_DETAILS.md",
            help="Detailed integrity report path. Defaults to CRM_DATA_INTEGRITY_DETAILS.md.",
        )
        parser.add_argument(
            "--csv-output",
            default="crm_integrity_export.csv",
            help="CSV export path. Defaults to crm_integrity_export.csv.",
        )
        parser.add_argument(
            "--csv-filter",
            default="broken",
            choices=("all", "broken", "legacy", "repairable"),
            help="Rows included in the CSV export. Defaults to broken.",
        )

    def handle(self, *args, **options):
        audit = build_opportunity_stage_audit()
        output_path = write_opportunity_stage_audit_report(Path(options["output"]), audit=audit)
        details_path = write_crm_data_integrity_details(Path(options["details_output"]), audit=audit)
        csv_path = write_crm_integrity_csv(
            Path(options["csv_output"]),
            audit=audit,
            filter_mode=options["csv_filter"],
        )
        notification_result = None
        if options["notify"]:
            notification_result = sync_opportunity_stage_audit_notification(audit)

        metrics = audit["metrics"]
        self.stdout.write(self.style.SUCCESS(f"Opportunity stage audit report: {output_path}"))
        self.stdout.write(
            "Workflow errors: {workflow_errors}; broken opportunities: {broken_opportunities}; "
            "broken production links: {broken_production_links}; broken invoice links: {broken_invoice_links}".format(
                **metrics
            )
        )
        self.stdout.write(self.style.SUCCESS(f"CRM data integrity details: {details_path}"))
        self.stdout.write(self.style.SUCCESS(f"CRM integrity CSV: {csv_path}"))
        if notification_result:
            state = "active" if notification_result["active"] else "resolved"
            self.stdout.write(f"CEO notification {state}: {notification_result['source_key']}")
