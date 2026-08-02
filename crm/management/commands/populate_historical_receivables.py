import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crm.services.historical_receivables import populate_historical_ledger, render_markdown_report


class Command(BaseCommand):
    help = "Dry-run or populate the dormant receivables ledger from immutable historical sources."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the evidence-safe subset. The default is read-only dry-run.",
        )
        parser.add_argument(
            "--allow-exceptions",
            action="store_true",
            help="Allow safe-subset writes when blocking exceptions exist. Use only for an isolated rehearsal.",
        )
        parser.add_argument(
            "--actor-username",
            default="",
            help="Existing user recorded as the migration actor. Required with --apply.",
        )
        parser.add_argument(
            "--batch-id",
            default="phase3c-historical-v1",
            help="Stable migration batch reference. Idempotency keys do not depend on this label.",
        )
        parser.add_argument("--json-output", default="", help="Optional JSON report path.")
        parser.add_argument("--markdown-output", default="", help="Optional Markdown report path.")

    def handle(self, *args, **options):
        apply = options["apply"]
        actor = None
        if apply:
            username = (options["actor_username"] or "").strip()
            if not username:
                raise CommandError("--actor-username is required with --apply.")
            actor = get_user_model().objects.filter(username=username, is_active=True).first()
            if not actor:
                raise CommandError("The migration actor does not exist or is inactive.")

        try:
            result = populate_historical_ledger(
                actor=actor,
                batch_id=(options["batch_id"] or "").strip() or "phase3c-historical-v1",
                apply=apply,
                allow_exceptions=options["allow_exceptions"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if options["json_output"]:
            path = Path(options["json_output"]).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result.as_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.stdout.write(f"JSON report: {path}")
        if options["markdown_output"]:
            path = Path(options["markdown_output"]).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_markdown_report(result), encoding="utf-8")
            self.stdout.write(f"Markdown report: {path}")

        self.stdout.write(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
        self.stdout.write(f"Invoices scanned: {result.invoices_scanned}")
        self.stdout.write(f"Payments scanned: {result.payments_scanned}")
        self.stdout.write(f"Events planned: {sum(result.events_planned.values())}")
        self.stdout.write(f"Events created: {sum(result.events_created.values())}")
        self.stdout.write(f"Events reused: {sum(result.events_reused.values())}")
        self.stdout.write(f"Allocations planned: {result.allocations_planned}")
        self.stdout.write(f"Allocations created: {result.allocations_created}")
        self.stdout.write(f"Allocations reused: {result.allocations_reused}")
        self.stdout.write(f"Blocking exceptions: {result.blocking_exception_count}")
        for row in result.reconciliation:
            self.stdout.write(
                "{currency}: outstanding diff={outstanding_difference}; "
                "payment diff={payment_difference}; accounting diff={accounting_difference}".format(**row)
            )
        status = "READY" if result.ready else "NOT READY"
        self.stdout.write(self.style.SUCCESS(status) if result.ready else self.style.WARNING(status))
