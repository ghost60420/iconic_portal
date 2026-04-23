from decimal import Decimal

from django.core.management.base import BaseCommand

from crm.models import AccountingEntry, ExchangeRate


class Command(BaseCommand):
    help = "Recalculate accounting amounts and fill missing rates for CAD/BDT entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report how many entries would be updated without saving.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit the number of entries to process.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        limit = int(options.get("limit") or 0)

        rate_row = ExchangeRate.objects.order_by("-updated_at").first()
        cad_to_bdt = Decimal("0")
        if rate_row and rate_row.cad_to_bdt and rate_row.cad_to_bdt > 0:
            cad_to_bdt = Decimal(str(rate_row.cad_to_bdt))

        qs = AccountingEntry.objects.all().order_by("id")
        if limit > 0:
            qs = qs[:limit]

        updated = 0
        checked = 0

        for entry in qs.iterator():
            checked += 1
            changed = False
            currency = (entry.currency or "").upper().strip()

            if currency == "CAD":
                if not entry.rate_to_cad or entry.rate_to_cad <= 0:
                    entry.rate_to_cad = Decimal("1")
                    changed = True
                if cad_to_bdt > 0 and (not entry.rate_to_bdt or entry.rate_to_bdt <= 0):
                    entry.rate_to_bdt = cad_to_bdt
                    changed = True
            elif currency == "BDT":
                if not entry.rate_to_bdt or entry.rate_to_bdt <= 0:
                    entry.rate_to_bdt = Decimal("1")
                    changed = True
                if cad_to_bdt > 0 and (not entry.rate_to_cad or entry.rate_to_cad <= 0):
                    entry.rate_to_cad = (Decimal("1") / cad_to_bdt).quantize(Decimal("0.000001"))
                    changed = True

            if changed:
                updated += 1
                if not dry_run:
                    entry.save(update_fields=["rate_to_cad", "rate_to_bdt", "amount_cad", "amount_bdt"])

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {updated} entries would be updated out of {checked} checked."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated {updated} entries out of {checked} checked."))
