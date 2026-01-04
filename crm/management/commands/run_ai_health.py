from django.core.management.base import BaseCommand
from crm.ai.health import run_and_store

class Command(BaseCommand):
    help = "Run AI Health Monitor checks and store results"

    def handle(self, *args, **options):
        run = run_and_store(created_by=None, notes="Daily scheduled run")
        self.stdout.write(self.style.SUCCESS(f"Saved AIHealthRun id={run.id} score={run.score}"))