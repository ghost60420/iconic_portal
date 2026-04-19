from django.core.management.base import BaseCommand

from leadbrain.models import LeadBrainUpload
from leadbrain.services.background_runner import launch_upload_processing, queue_parse_upload


class Command(BaseCommand):
    help = "Queue Lead Brain Lite uploads for background parsing or processing."

    def add_arguments(self, parser):
        parser.add_argument("--upload", type=int, default=None)
        parser.add_argument("--retry-failed-only", action="store_true")

    def handle(self, *args, **options):
        upload_id = options.get("upload")
        retry_failed_only = options.get("retry_failed_only", False)

        queryset = LeadBrainUpload.objects.all().order_by("uploaded_at", "id")
        if upload_id:
            queryset = queryset.filter(pk=upload_id)
        else:
            queryset = queryset.filter(
                status__in=[
                    LeadBrainUpload.STATUS_QUEUED,
                    LeadBrainUpload.STATUS_PARSING,
                    LeadBrainUpload.STATUS_PROCESSING,
                    LeadBrainUpload.STATUS_FAILED,
                    LeadBrainUpload.STATUS_PARTIAL,
                ]
            )

        queued = 0
        for upload in queryset:
            if retry_failed_only:
                self.stdout.write(self.style.NOTICE(f"Queueing failed rows for upload {upload.pk}"))
                launch_upload_processing(upload.pk)
            elif upload.total_rows:
                self.stdout.write(self.style.NOTICE(f"Queueing processing for upload {upload.pk}"))
                launch_upload_processing(upload.pk)
            else:
                self.stdout.write(self.style.NOTICE(f"Queueing parse for upload {upload.pk}"))
                queue_parse_upload(upload.pk)
            queued += 1

        self.stdout.write(self.style.SUCCESS(f"Queued {queued} Lead Brain upload job(s)."))
