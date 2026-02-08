from django.conf import settings
from django.core.management.base import BaseCommand

from marketing.ai.engine import generate_insights


class Command(BaseCommand):
    help = "Generate marketing insights (rule-based)."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_ENABLED", False):
            self.stdout.write("MARKETING_ENABLED is off. Skipping.")
            return

        generate_insights()
        self.stdout.write(self.style.SUCCESS("Insights generated."))
