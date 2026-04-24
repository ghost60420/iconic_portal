from django.conf import settings
from django.core.management.base import BaseCommand

from marketing.ai.engine import generate_insights


class Command(BaseCommand):
    help = "Generate marketing insights (rule-based with optional safe LLM fallback)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_ENABLED", False):
            self.stdout.write("MARKETING_ENABLED is off. Skipping.")
            return

        days = max(int(options.get("days") or 30), 7)
        generate_insights(days=days)
        self.stdout.write(self.style.SUCCESS(f"Insights generated for the last {days} days."))
