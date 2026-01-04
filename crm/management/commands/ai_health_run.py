from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from crm.ai.health import run_and_store

class Command(BaseCommand):
    help = "Run AI health checks and store result"

    def add_arguments(self, parser):
        parser.add_argument("--user_id", type=int, default=None)
        parser.add_argument("--notes", type=str, default="Scheduled run")

    def handle(self, *args, **options):
        user_id = options["user_id"]
        notes = options["notes"]

        user = None
        if user_id:
            User = get_user_model()
            user = User.objects.filter(id=user_id).first()

        run = run_and_store(created_by=user, notes=notes)
        self.stdout.write(self.style.SUCCESS(f"Saved AI health run {run.id} score={run.score}"))