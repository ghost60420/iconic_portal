from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the daily social platform syncs for Meta, LinkedIn, and TikTok."

    commands = [
        "marketing_sync_meta_daily",
        "marketing_sync_linkedin_daily",
        "marketing_sync_tiktok_daily",
    ]

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_ENABLED", False):
            self.stdout.write("MARKETING_ENABLED is off. Skipping.")
            return

        for command in self.commands:
            self.stdout.write(f"Running {command}...")
            call_command(command, stdout=self.stdout, stderr=self.stderr)

        self.stdout.write(self.style.SUCCESS("Social Connections daily sync complete."))
