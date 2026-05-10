from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Alias for marketing_sync_meta_daily."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", default="")
        parser.add_argument("--platform", default="")

    def handle(self, *args, **options):
        kwargs = {}
        if options.get("account_id"):
            kwargs["account_id"] = options["account_id"]
        if options.get("platform"):
            kwargs["platform"] = options["platform"]
        call_command("marketing_sync_meta_daily", **kwargs)
