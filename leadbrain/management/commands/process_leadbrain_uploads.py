from django.core.management.base import BaseCommand

from leadbrain.models import LeadBrainUpload
from leadbrain.services.processing_service import process_upload_batch, select_next_upload, update_upload_note


class Command(BaseCommand):
    help = "Process queued Lead Brain Lite uploads in background batches."

    def add_arguments(self, parser):
        parser.add_argument("--upload", type=int, default=None)
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args, **options):
        upload_id = options.get("upload")
        limit = max(1, options.get("limit") or 1)
        batch_size = max(1, options.get("batch_size") or 100)

        processed_uploads = 0
        while processed_uploads < limit:
            upload = select_next_upload(upload_id=upload_id)
            if not upload:
                break
            self.stdout.write(self.style.NOTICE(f"Processing Lead Brain upload {upload.pk}"))
            upload.refresh_progress()
            update_upload_note(upload)

            while True:
                processed_rows = process_upload_batch(upload, batch_size=batch_size)
                if not processed_rows:
                    upload.refresh_progress()
                    update_upload_note(upload)
                    break
            processed_uploads += 1
            if upload_id:
                break
