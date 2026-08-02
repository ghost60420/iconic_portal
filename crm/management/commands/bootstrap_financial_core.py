from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crm.services.chart_of_accounts import ChartOfAccountsError, bootstrap_chart_of_accounts


class Command(BaseCommand):
    help = "Create the approved Financial Core Chart of Accounts and expense categories."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True, help="Username recorded as the configuration actor.")
        parser.add_argument("--apply", action="store_true", help="Persist configuration. Default is a rolled-back dry run.")

    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(username=options["actor"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError("Actor username does not exist.") from exc
        try:
            result = bootstrap_chart_of_accounts(actor=actor, dry_run=not options["apply"])
        except ChartOfAccountsError as exc:
            raise CommandError(str(exc)) from exc
        mode = "APPLIED" if options["apply"] else "DRY RUN (rolled back)"
        self.stdout.write(self.style.SUCCESS(f"{mode}: {result}"))
