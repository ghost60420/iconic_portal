from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from crm.services.kpi_release import KPIReleaseError, prepare_release_drafts


class Command(BaseCommand):
    help = (
        "Create the idempotent Stage 10 KPI policy and role-template drafts. "
        "Nothing is approved, published, assigned, or scheduled."
    )

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True, help="CEO or Super Admin username.")
        parser.add_argument(
            "--effective-date",
            required=True,
            help="Draft effective date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--currency",
            required=True,
            help="Three-letter currency code for the draft bonus rule.",
        )

    def handle(self, *args, **options):
        effective_date = parse_date(options["effective_date"])
        if effective_date is None:
            raise CommandError("--effective-date must use YYYY-MM-DD format.")
        actor = get_user_model().objects.filter(
            username=options["actor"],
            is_active=True,
        ).first()
        if actor is None:
            raise CommandError("--actor must identify an active user.")
        try:
            draft_set = prepare_release_drafts(
                effective_date=effective_date,
                currency=options["currency"],
                actor=actor,
            )
        except (KPIReleaseError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Prepared {len(draft_set.template_versions)} KPI role-template "
                f"drafts and {len(draft_set.approvals)} Draft approval records."
            )
        )
        self.stdout.write(
            "Bonus, intelligence, and notification policies remain unpublished. "
            "No employee assignments or automation schedules were activated."
        )
