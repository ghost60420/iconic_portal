from django.core.management.base import BaseCommand

from leadbrain.services.discovery_service import schedule_due_discovery_runs


class Command(BaseCommand):
    help = "Schedule due Lead Brain discovery jobs into queued discovery runs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
        limit = int(options.get("limit") or 0) or None
        runs = schedule_due_discovery_runs(limit=limit)
        if not runs:
            self.stdout.write("No due discovery jobs were scheduled.")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Scheduled {len(runs)} discovery run(s): {', '.join(str(run.pk) for run in runs)}"
            )
        )
