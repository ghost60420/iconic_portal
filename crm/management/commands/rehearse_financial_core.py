from pathlib import Path

from django.core.management.base import BaseCommand

from crm.services.financial_historical_reconciliation import (
    build_financial_historical_exception_plan,
    render_financial_exception_report,
)


class Command(BaseCommand):
    help = "Generate a read-only Financial Core exception report from the current database (use a database copy)."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Markdown report path.")

    def handle(self, *args, **options):
        exceptions = build_financial_historical_exception_plan()
        output = Path(options["output"]).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_financial_exception_report(exceptions), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(exceptions)} exception(s) to {output}"))
