from django.core.management.base import BaseCommand
from leadbrain.services.repair_service import repair_uploads


STALE_MINUTES_DEFAULT = 60


class Command(BaseCommand):
    help = "Inspect or repair stale and duplicate Lead Brain Lite uploads."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply the repair actions.")
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=STALE_MINUTES_DEFAULT,
            help="Mark processing uploads older than this threshold as stale.",
        )
        parser.add_argument(
            "--flag-duplicates",
            action="store_true",
            help="Flag older duplicate upload jobs for review without deleting them.",
        )
        parser.add_argument(
            "--backfill-hashes",
            action="store_true",
            help="Compute missing file hashes for older uploads before duplicate review.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        stale_minutes = max(1, options["stale_minutes"])
        flag_duplicates = options["flag_duplicates"]
        backfill_hashes = options["backfill_hashes"]
        result = repair_uploads(
            apply_changes=apply_changes,
            stale_minutes=stale_minutes,
            flag_duplicates=flag_duplicates,
            backfill_hashes=backfill_hashes,
        )

        self.stdout.write(f"STALE_UPLOADS {result['stale_uploads']}")
        self.stdout.write(f"BACKFILLED_HASHES {result['backfilled_hashes']}")
        self.stdout.write(f"DUPLICATE_GROUPS {result['duplicate_groups']}")
        if result["flagged_upload_ids"]:
            self.stdout.write(f"FLAGGED_UPLOADS {result['flagged_upload_ids']}")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to update records."))
