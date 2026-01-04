# crm/management/commands/ai_health_job.py
from django.core.management.base import BaseCommand
from crm.ai.health import run_and_store

class Command(BaseCommand):
    help = "Run AI health and store result"

    def handle(self, *args, **kwargs):
        run_and_store(created_by=None, notes="Background job")
        self.stdout.write("AI health job done")