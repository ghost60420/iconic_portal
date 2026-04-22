from django.core.management.base import BaseCommand

from leadbrain.services.cleanup_service import cleanup_leadbrain_data


class Command(BaseCommand):
    help = "Archive failed Lead Brain uploads and duplicate company rows while preserving history."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply the cleanup changes.")

    def handle(self, *args, **options):
        result = cleanup_leadbrain_data(apply_changes=options["apply"])

        self.stdout.write(f"FAILED_UPLOADS_FOUND {result.failed_uploads_found}")
        self.stdout.write(f"FAILED_UPLOADS_ARCHIVED {result.failed_uploads_archived}")
        self.stdout.write(f"DUPLICATE_WEBSITE_GROUPS {result.duplicate_groups_found}")
        self.stdout.write(f"DUPLICATE_ROWS_FOUND {result.duplicate_rows_found}")
        self.stdout.write(f"DUPLICATE_ROWS_ARCHIVED {result.duplicate_rows_archived}")
        if result.kept_company_ids:
            self.stdout.write(f"KEPT_COMPANY_IDS {result.kept_company_ids}")
        if result.archived_company_ids:
            self.stdout.write(f"ARCHIVED_COMPANY_IDS {result.archived_company_ids}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to archive Lead Brain data."))
