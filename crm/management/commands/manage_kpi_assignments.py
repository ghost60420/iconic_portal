from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from crm.services.kpi_release import (
    KPIReleaseError,
    activate_assignment_draft,
    preview_assignment_plan,
    save_assignment_draft,
)


class Command(BaseCommand):
    help = (
        "Preview, save inactive drafts, or explicitly activate KPI role "
        "assignments. No employee is assigned automatically."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--action",
            required=True,
            choices=("preview", "save-draft", "activate"),
        )
        parser.add_argument("--actor", required=True, help="CEO or Super Admin username.")
        parser.add_argument("--employee-id", type=int)
        parser.add_argument("--start-date", help="YYYY-MM-DD")
        parser.add_argument(
            "--role",
            action="append",
            default=[],
            metavar="TEMPLATE_ID:WEIGHT:MANAGER_ID_OR_NONE:BONUS_YES_OR_NO",
            help="Repeat once for each KPI role.",
        )
        parser.add_argument(
            "--assignment-id",
            action="append",
            type=int,
            default=[],
            help="Repeat for each inactive assignment draft to activate.",
        )
        parser.add_argument("--notes", default="")
        parser.add_argument("--reason", default="")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required to activate assignment drafts.",
        )

    @staticmethod
    def _parse_roles(values, notes):
        roles = []
        for value in values:
            parts = value.split(":")
            if len(parts) != 4:
                raise CommandError(
                    "--role must use TEMPLATE_ID:WEIGHT:MANAGER_ID_OR_NONE:"
                    "BONUS_YES_OR_NO."
                )
            template_id, raw_weight, raw_manager, raw_bonus = parts
            try:
                weight = Decimal(raw_weight)
                template_id = int(template_id)
                manager = (
                    None
                    if raw_manager.strip().lower() in {"", "none", "null"}
                    else int(raw_manager)
                )
            except (InvalidOperation, ValueError) as exc:
                raise CommandError("--role contains an invalid ID or weight.") from exc
            bonus_value = raw_bonus.strip().lower()
            if bonus_value not in {"yes", "no", "true", "false", "1", "0"}:
                raise CommandError("Bonus eligibility must be yes or no.")
            roles.append(
                {
                    "kpi_template": template_id,
                    "role_weight": weight,
                    "manager": manager,
                    "bonus_eligible": bonus_value in {"yes", "true", "1"},
                    "notes": notes,
                }
            )
        return roles

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(
            username=options["actor"],
            is_active=True,
        ).first()
        if actor is None:
            raise CommandError("--actor must identify an active user.")
        if options["action"] == "activate":
            if not options["confirm"]:
                raise CommandError("Activation requires --confirm.")
            try:
                rows = activate_assignment_draft(
                    assignment_ids=options["assignment_id"],
                    actor=actor,
                    reason=options["reason"],
                )
            except (KPIReleaseError, ValidationError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                self.style.SUCCESS(f"Activated {len(rows)} KPI assignment(s).")
            )
            return

        start_date = parse_date(options.get("start_date") or "")
        if options.get("employee_id") is None or start_date is None:
            raise CommandError(
                "Preview and save-draft require --employee-id and "
                "--start-date YYYY-MM-DD."
            )
        roles = self._parse_roles(options["role"], options["notes"])
        try:
            preview = preview_assignment_plan(
                employee=options["employee_id"],
                assignments=roles,
                start_date=start_date,
                actor=actor,
            )
            if options["action"] == "save-draft":
                rows = save_assignment_draft(
                    employee=preview.employee,
                    assignments=roles,
                    start_date=start_date,
                    actor=actor,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Saved {len(rows)} inactive assignment draft(s)."
                    )
                )
                return
        except (KPIReleaseError, ValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Assignment preview for employee {preview.employee.pk}: "
                f"{preview.total_weight:.2f}% across {len(preview.roles)} role(s)."
            )
        )
        for role in preview.roles:
            manager_id = role["manager"].pk if role["manager"] else "none"
            self.stdout.write(
                f"- {role['template'].name}: {role['role_weight']:.2f}% "
                f"manager={manager_id}, items={len(role['items'])}"
            )
