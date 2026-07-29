from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from crm.models_kpi_release import KPIPolicyApproval
from crm.services.kpi_release import (
    KPIReleaseError,
    approve_policy,
    create_policy_successor,
    publish_policy,
    retire_policy,
    submit_policy_for_review,
)


class Command(BaseCommand):
    help = "Transition one Stage 10 KPI policy through the controlled workflow."

    def add_arguments(self, parser):
        parser.add_argument(
            "--action",
            required=True,
            choices=("submit", "approve", "publish", "retire", "new-version"),
        )
        parser.add_argument("--policy-id", required=True, type=int)
        parser.add_argument("--actor", required=True, help="Active CRM username.")
        parser.add_argument(
            "--reason",
            default="",
            help="Required for submit, approve, and retire.",
        )
        parser.add_argument(
            "--effective-date",
            help="Required for new-version; use YYYY-MM-DD.",
        )

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(
            username=options["actor"],
            is_active=True,
        ).first()
        if actor is None:
            raise CommandError("--actor must identify an active user.")
        approval = KPIPolicyApproval.service_objects.filter(
            pk=options["policy_id"]
        ).first()
        if approval is None:
            raise CommandError("--policy-id does not identify a KPI policy.")
        if options["action"] == "new-version":
            effective_date = parse_date(options.get("effective_date") or "")
            if effective_date is None:
                raise CommandError(
                    "new-version requires --effective-date YYYY-MM-DD."
                )
            try:
                successor = create_policy_successor(
                    approval,
                    effective_date=effective_date,
                    actor=actor,
                )
            except (KPIReleaseError, ValidationError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created Draft successor policy {successor.pk}."
                )
            )
            return

        transition = {
            "submit": submit_policy_for_review,
            "approve": approve_policy,
            "publish": publish_policy,
            "retire": retire_policy,
        }[options["action"]]
        kwargs = {"actor": actor}
        if options["action"] != "publish":
            kwargs["reason"] = options["reason"]
        try:
            updated = transition(approval, **kwargs)
        except (KPIReleaseError, ValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Policy {updated.pk} is now {updated.get_status_display()}."
            )
        )
