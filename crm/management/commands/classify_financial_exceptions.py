from pathlib import Path

from django.core.management.base import BaseCommand

from crm.services.financial_exception_classification import (
    build_exception_classification_rows,
    render_exception_classification_report,
)


class Command(BaseCommand):
    help = "Classify historical financial exceptions without changing financial records."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Optional Markdown output path.")

    def handle(self, *args, **options):
        rows = build_exception_classification_rows()
        report = render_exception_classification_report(rows)
        output = options.get("output")
        if output:
            path = Path(output).expanduser().resolve()
            path.write_text(report, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} classifications to {path}"))
        else:
            self.stdout.write(report)
