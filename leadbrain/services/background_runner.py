from math import ceil

from django.conf import settings

from leadbrain.models import LeadBrainCompany, LeadBrainUpload


def queue_parse_upload(upload_id: int) -> None:
    from leadbrain.tasks import parse_upload_job

    parse_upload_job.delay(upload_id)


def queue_processing_batches(upload_id: int, *, slots: int | None = None) -> int:
    from leadbrain.tasks import process_upload_batch_job

    if slots is None:
        upload = LeadBrainUpload.objects.filter(pk=upload_id).only("pending_rows", "failed_rows").first()
        queued_rows = 0
        if upload:
            queued_rows = (upload.pending_rows or 0) + (upload.failed_rows or 0)
        if not queued_rows:
            queued_rows = LeadBrainCompany.objects.filter(
                upload_id=upload_id,
                research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_FAILED],
            ).count()
        if queued_rows:
            batch_size = max(1, int(getattr(settings, "LEADBRAIN_PROCESS_BATCH_SIZE", 20)))
            slots = min(
                int(getattr(settings, "LEADBRAIN_CELERY_FANOUT", 4)),
                max(1, ceil(queued_rows / batch_size)),
            )
        else:
            slots = 1

    fanout = max(1, slots)
    for _ in range(fanout):
        process_upload_batch_job.delay(upload_id)
    return fanout


def launch_upload_processing(upload_id: int) -> int:
    return queue_processing_batches(upload_id)
