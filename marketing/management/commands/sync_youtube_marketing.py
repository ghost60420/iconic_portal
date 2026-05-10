from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Alias for marketing_sync_youtube_daily."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", default="")

    def handle(self, *args, **options):
        kwargs = {}
        if options.get("account_id"):
            kwargs["account_id"] = options["account_id"]
        call_command("marketing_sync_youtube_daily", **kwargs)
