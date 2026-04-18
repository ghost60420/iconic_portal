from django.core.management.base import BaseCommand
from django.db import transaction

from leadbrain.models import LeadBrainCompany, LeadBrainUpload, LeadBrainWorker


class Command(BaseCommand):
    help = "Safely clear Lead Brain Lite data only."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete Lead Brain Lite data.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        upload_count = LeadBrainUpload.objects.count()
        company_count = LeadBrainCompany.objects.count()
        worker_count = LeadBrainWorker.objects.count()

        self.stdout.write(f"UPLOADS {upload_count}")
        self.stdout.write(f"COMPANIES {company_count}")
        self.stdout.write(f"WORKERS {worker_count}")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to delete Lead Brain Lite data."))
            return

        with transaction.atomic():
            for upload in LeadBrainUpload.objects.only("id", "file"):
                if upload.file:
                    upload.file.delete(save=False)
            LeadBrainUpload.objects.all().delete()
            LeadBrainWorker.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("Lead Brain Lite data was cleared."))
