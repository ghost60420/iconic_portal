from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from crm.services.expense_management import ExpenseManagementError, generate_recurring_expense_drafts


class Command(BaseCommand):
    help = "Generate draft recurring expenses through a date. Drafts are never posted or marked paid."

    def add_arguments(self, parser):
        parser.add_argument("--through", required=True, help="Last occurrence date in YYYY-MM-DD format.")
        parser.add_argument("--actor", required=True, help="Username recorded as the draft creator.")

    def handle(self, *args, **options):
        through_date = parse_date(options["through"])
        if not through_date:
            raise CommandError("--through must be YYYY-MM-DD.")
        try:
            actor = get_user_model().objects.get(username=options["actor"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError("Actor username does not exist.") from exc
        try:
            created = generate_recurring_expense_drafts(through_date=through_date, actor=actor)
        except ExpenseManagementError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} draft expense(s); none were posted or paid."))
