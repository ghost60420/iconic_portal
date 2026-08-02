from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crm.services.financial_exception_review import load_exception_reviews


class Command(BaseCommand):
    help = "Load historical financial exceptions into the review center; dry-run unless --apply is supplied."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--actor", default="")

    def handle(self, *args, **options):
        actor = None
        if options["apply"]:
            username = options["actor"].strip()
            if not username:
                raise CommandError("--actor is required with --apply")
            actor = get_user_model().objects.filter(username=username, is_active=True).first()
            if not actor:
                raise CommandError("The requested active actor was not found.")
        result = load_exception_reviews(actor=actor, apply=options["apply"])
        mode = "APPLIED" if options["apply"] else "DRY RUN"
        self.stdout.write(
            f"{mode}: planned={result['planned']} critical={result['critical']} high={result['high']} "
            f"existing={result['existing']} would_create={result['would_create']} created={result['created']}"
        )
