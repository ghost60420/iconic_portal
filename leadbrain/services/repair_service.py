import hashlib
from datetime import timedelta

from django.db import models
from django.utils import timezone

from leadbrain.models import LeadBrainCompany, LeadBrainUpload


def compute_file_hash(upload: LeadBrainUpload) -> str:
    if not upload.file:
        return ""
    digest = hashlib.sha256()
    with upload.file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def repair_uploads(
    *,
    apply_changes: bool,
    stale_minutes: int,
    flag_duplicates: bool,
    backfill_hashes: bool,
):
    stale_cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    stale_uploads = LeadBrainUpload.objects.filter(
        status__in=[
            LeadBrainUpload.STATUS_QUEUED,
            LeadBrainUpload.STATUS_PARSING,
            LeadBrainUpload.STATUS_PROCESSING,
        ],
        updated_at__lt=stale_cutoff,
    ).order_by("updated_at", "id")

    result = {
        "stale_uploads": stale_uploads.count(),
        "stale_upload_ids": list(stale_uploads.values_list("id", flat=True)),
        "backfilled_hashes": 0,
        "duplicate_groups": 0,
        "flagged_upload_ids": [],
    }

    for upload in stale_uploads:
        if not apply_changes:
            continue

        stale_rows = upload.companies.filter(
            research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_PROCESSING]
        )
        stale_rows.update(
            research_status=LeadBrainCompany.STATUS_FAILED,
            research_error=f"Marked failed by repair_leadbrain_uploads after {stale_minutes} stale minutes.",
            processed_at=timezone.now(),
        )
        upload.refresh_progress(save=False)
        upload.status = LeadBrainUpload.STATUS_PARTIAL if upload.completed_rows else LeadBrainUpload.STATUS_FAILED
        upload.status_note = f"Marked failed by repair_leadbrain_uploads after {stale_minutes} stale minutes."
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

    if backfill_hashes:
        missing_hashes = LeadBrainUpload.objects.filter(file_hash="").order_by("-uploaded_at", "-id")
        for upload in missing_hashes:
            try:
                file_hash = compute_file_hash(upload)
            except Exception:
                continue
            if not file_hash:
                continue
            if apply_changes:
                active_conflict = (
                    LeadBrainUpload.objects.filter(
                        uploaded_by_id=upload.uploaded_by_id,
                        file_hash=file_hash,
                        status__in=[
                            LeadBrainUpload.STATUS_QUEUED,
                            LeadBrainUpload.STATUS_PARSING,
                            LeadBrainUpload.STATUS_PROCESSING,
                        ],
                    )
                    .exclude(pk=upload.pk)
                    .order_by("-uploaded_at", "-id")
                    .first()
                )
                if active_conflict and upload.status in [
                    LeadBrainUpload.STATUS_QUEUED,
                    LeadBrainUpload.STATUS_PARSING,
                    LeadBrainUpload.STATUS_PROCESSING,
                ]:
                    upload.status = LeadBrainUpload.STATUS_CANCELLED
                    upload.status_note = (
                        f"Duplicate upload history for review. Newer active upload job is #{active_conflict.pk}."
                    )
                    upload.save(update_fields=["status", "status_note", "updated_at"])
                upload.file_hash = file_hash
                upload.save(update_fields=["file_hash", "updated_at"])
            result["backfilled_hashes"] += 1

    duplicate_groups = list(
        LeadBrainUpload.objects.exclude(file_hash="")
        .values("uploaded_by_id", "file_hash")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
        .order_by("uploaded_by_id", "file_hash")
    )
    result["duplicate_groups"] = len(duplicate_groups)

    for group in duplicate_groups:
        uploads = list(
            LeadBrainUpload.objects.filter(
                uploaded_by_id=group["uploaded_by_id"],
                file_hash=group["file_hash"],
            ).order_by("-uploaded_at", "-id")
        )
        newest = uploads[0]
        older_ids = [upload.pk for upload in uploads[1:]]
        if apply_changes and flag_duplicates and older_ids:
            note = f"Duplicate upload history for review. Newer upload job is #{newest.pk}."
            LeadBrainUpload.objects.filter(pk__in=older_ids).update(status_note=note, updated_at=timezone.now())
            result["flagged_upload_ids"].extend(older_ids)

    return result
