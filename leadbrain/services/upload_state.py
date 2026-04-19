from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from leadbrain.models import LeadBrainCompany, LeadBrainUpload


ACTIVE_UPLOAD_STATUSES = [
    LeadBrainUpload.STATUS_QUEUED,
    LeadBrainUpload.STATUS_PARSING,
    LeadBrainUpload.STATUS_PROCESSING,
]


def stale_cutoff():
    minutes = max(1, int(getattr(settings, "LEADBRAIN_STALE_MINUTES", 10)))
    return timezone.now() - timedelta(minutes=minutes)


def is_upload_stale(upload: LeadBrainUpload) -> bool:
    if upload.status not in ACTIVE_UPLOAD_STATUSES:
        return False
    if not upload.updated_at:
        return False
    return upload.updated_at < stale_cutoff()


def release_stale_upload(upload: LeadBrainUpload, *, reason: str | None = None) -> LeadBrainUpload:
    if not is_upload_stale(upload):
        return upload

    stale_reason = reason or "Marked failed after no Lead Brain progress was detected."
    now = timezone.now()
    upload.companies.filter(
        research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_PROCESSING]
    ).update(
        research_status=LeadBrainCompany.STATUS_FAILED,
        research_error=stale_reason,
        processed_at=now,
        updated_at=now,
    )
    upload.refresh_progress(save=False)
    upload.status = LeadBrainUpload.STATUS_PARTIAL if upload.completed_rows else LeadBrainUpload.STATUS_FAILED
    upload.status_note = stale_reason[:2000]
    upload.save(
        update_fields=[
            "row_count",
            "total_rows",
            "pending_rows",
            "processing_rows",
            "completed_rows",
            "failed_rows",
            "progress_percent",
            "status",
            "status_note",
            "updated_at",
        ]
    )
    return upload


def find_active_duplicate_upload(
    *,
    user_id: int | None,
    file_hash: str = "",
    file_name: str = "",
    file_size: int = 0,
    exclude_pk: int | None = None,
):
    if not user_id:
        return None

    queryset = LeadBrainUpload.objects.filter(uploaded_by_id=user_id, status__in=ACTIVE_UPLOAD_STATUSES)
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)

    if file_hash:
        queryset = queryset.filter(file_hash=file_hash)
    elif file_name and file_size:
        queryset = queryset.filter(file_name=file_name, file_size=file_size)
    else:
        return None

    for upload in queryset.order_by("-uploaded_at", "-id"):
        if is_upload_stale(upload):
            release_stale_upload(
                upload,
                reason="Marked failed after no Lead Brain progress was detected. You can retry or delete it now.",
            )
            continue
        return upload
    return None
