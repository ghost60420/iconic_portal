import hashlib
import logging

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.utils import OperationalError
from django.utils import timezone

from leadbrain.models import LeadBrainCompany, LeadBrainUpload
from leadbrain.services.file_parser import parse_uploaded_file_report
from leadbrain.services.import_service import prepare_import_rows
from leadbrain.services.processing_service import process_upload_batch, update_upload_note


logger = logging.getLogger(__name__)
ACTIVE_UPLOAD_STATUSES = [
    LeadBrainUpload.STATUS_QUEUED,
    LeadBrainUpload.STATUS_PARSING,
    LeadBrainUpload.STATUS_PROCESSING,
]


def _compute_upload_file_hash(upload: LeadBrainUpload) -> str:
    if not upload.file:
        return ""
    digest = hashlib.sha256()
    with upload.file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _build_status_note(*, imported_rows: int, source_rows: int, duplicates: int, invalid_rows: int, blank_rows: int) -> str:
    parts = [
        f"Imported {imported_rows} of {source_rows} row(s).",
        f"Skipped {duplicates} duplicate row(s).",
        f"Ignored {invalid_rows} invalid row(s).",
    ]
    if blank_rows:
        parts.append(f"Ignored {blank_rows} blank row(s).")
    parts.append("Background research has been queued.")
    return " ".join(parts)


def _mark_upload_failed(upload: LeadBrainUpload, note: str) -> None:
    upload.status = LeadBrainUpload.STATUS_FAILED
    upload.status_note = note[:2000]
    upload.save(update_fields=["status", "status_note", "updated_at"])


def _mark_upload_cancelled(upload: LeadBrainUpload, note: str) -> None:
    upload.status = LeadBrainUpload.STATUS_CANCELLED
    upload.status_note = note[:2000]
    upload.save(update_fields=["status", "status_note", "updated_at"])


@shared_task(
    bind=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    queue="leadbrain",
)
def parse_upload_job(self, upload_id: int):
    close_old_connections()
    upload = LeadBrainUpload.objects.filter(pk=upload_id).first()
    if not upload:
        return "missing"
    if upload.status == LeadBrainUpload.STATUS_CANCELLED:
        return "cancelled"

    LeadBrainUpload.objects.filter(pk=upload.pk, status__in=[LeadBrainUpload.STATUS_QUEUED, LeadBrainUpload.STATUS_FAILED]).update(
        status=LeadBrainUpload.STATUS_PARSING,
        status_note="Upload parsing is running in the background.",
        updated_at=timezone.now(),
    )
    upload.refresh_from_db()
    if upload.status == LeadBrainUpload.STATUS_CANCELLED:
        return "cancelled"

    try:
        file_hash = _compute_upload_file_hash(upload)
        upload.file_hash = file_hash
        upload.save(update_fields=["file_hash", "updated_at"])
    except IntegrityError:
        duplicate_upload = (
            LeadBrainUpload.objects.filter(
                uploaded_by_id=upload.uploaded_by_id,
                file_hash=upload.file_hash,
                status__in=ACTIVE_UPLOAD_STATUSES,
            )
            .exclude(pk=upload.pk)
            .order_by("-uploaded_at", "-id")
            .first()
        )
        _mark_upload_cancelled(
            upload,
            f"This file is already processing under upload job #{duplicate_upload.pk}."
            if duplicate_upload
            else "This file is already processing under another upload job.",
        )
        return "duplicate"

    duplicate_upload = (
        LeadBrainUpload.objects.filter(
            uploaded_by_id=upload.uploaded_by_id,
            file_hash=upload.file_hash,
            status__in=ACTIVE_UPLOAD_STATUSES,
        )
        .exclude(pk=upload.pk)
        .order_by("-uploaded_at", "-id")
        .first()
    )
    if duplicate_upload:
        _mark_upload_cancelled(upload, f"This file is already processing under upload job #{duplicate_upload.pk}.")
        return "duplicate"

    try:
        parse_report = parse_uploaded_file_report(upload.file.path)
        import_report = prepare_import_rows(parse_report["rows"])
    except Exception as exc:
        logger.exception("leadbrain parse task failed for upload %s", upload.pk)
        _mark_upload_failed(upload, f"The uploaded file could not be parsed. {exc}")
        return "failed"

    import_rows = import_report["rows"]
    imported_rows = import_report["imported_rows"]
    skipped_duplicate_rows = import_report["skipped_duplicate_rows"]
    invalid_rows = import_report["invalid_rows"]
    invalid_reasons = import_report["invalid_reasons"]
    blank_rows = parse_report.get("blank_rows", 0)
    source_row_count = parse_report.get("source_row_count", 0)
    batch_size = max(1, int(getattr(settings, "LEADBRAIN_PARSE_BATCH_SIZE", 500)))

    companies = [
        LeadBrainCompany(
            upload=upload,
            row_number=row.get("row_number", 0),
            company_name=row.get("company_name", ""),
            website=row.get("website", ""),
            email=row.get("email", ""),
            phone=row.get("phone", ""),
            country=row.get("country", ""),
            city=row.get("city", ""),
            raw_row_json=row.get("raw_row_json", {}),
            fit_label="",
            fit_score=0,
            suggested_action="Queued for Research",
            research_status=LeadBrainCompany.STATUS_PENDING,
        )
        for row in import_rows
    ]

    with transaction.atomic():
        upload.companies.all().delete()
        for start in range(0, len(companies), batch_size):
            LeadBrainCompany.objects.bulk_create(companies[start : start + batch_size], batch_size=batch_size)

        upload.row_count = imported_rows
        upload.source_row_count = source_row_count
        upload.total_rows = imported_rows
        upload.imported_rows = imported_rows
        upload.skipped_duplicate_rows = skipped_duplicate_rows
        upload.invalid_rows = invalid_rows
        upload.pending_rows = imported_rows
        upload.processing_rows = 0
        upload.completed_rows = 0
        upload.failed_rows = 0
        upload.progress_percent = 0
        upload.detected_columns_json = parse_report.get("detected_columns", [])
        upload.sample_rows_json = parse_report.get("sample_rows", [])
        upload.invalid_row_examples_json = invalid_reasons
        upload.status = LeadBrainUpload.STATUS_PROCESSING if imported_rows else LeadBrainUpload.STATUS_FAILED
        upload.status_note = (
            _build_status_note(
                imported_rows=imported_rows,
                source_rows=source_row_count,
                duplicates=skipped_duplicate_rows,
                invalid_rows=invalid_rows,
                blank_rows=blank_rows,
            )
            if imported_rows
            else f"No rows were imported. Skipped {skipped_duplicate_rows} duplicate row(s), ignored {invalid_rows} invalid row(s), and ignored {blank_rows} blank row(s)."
        )
        upload.save(
            update_fields=[
                "row_count",
                "source_row_count",
                "total_rows",
                "imported_rows",
                "skipped_duplicate_rows",
                "invalid_rows",
                "pending_rows",
                "processing_rows",
                "completed_rows",
                "failed_rows",
                "progress_percent",
                "detected_columns_json",
                "sample_rows_json",
                "invalid_row_examples_json",
                "status",
                "status_note",
                "updated_at",
            ]
        )

    if not imported_rows:
        return "empty"

    from leadbrain.services.background_runner import queue_processing_batches

    queue_processing_batches(upload.pk)
    return "queued"


@shared_task(
    bind=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    queue="leadbrain",
)
def process_upload_batch_job(self, upload_id: int):
    close_old_connections()
    upload = LeadBrainUpload.objects.filter(pk=upload_id).first()
    if not upload:
        return 0
    if upload.status in [LeadBrainUpload.STATUS_CANCELLED, LeadBrainUpload.STATUS_COMPLETE]:
        return 0
    if upload.status == LeadBrainUpload.STATUS_PARSING:
        return 0
    if upload.status == LeadBrainUpload.STATUS_QUEUED:
        LeadBrainUpload.objects.filter(pk=upload.pk).update(
            status=LeadBrainUpload.STATUS_PROCESSING,
            status_note="Background research and scoring are running.",
            updated_at=timezone.now(),
        )
        upload.refresh_from_db()

    batch_size = max(1, int(getattr(settings, "LEADBRAIN_PROCESS_BATCH_SIZE", 20)))
    processed_rows = process_upload_batch(upload, batch_size=batch_size)
    upload.refresh_from_db()

    if upload.status == LeadBrainUpload.STATUS_CANCELLED:
        return processed_rows

    pending_exists = upload.companies.filter(
        research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_FAILED]
    ).exists()

    if pending_exists and upload.status not in [LeadBrainUpload.STATUS_FAILED, LeadBrainUpload.STATUS_CANCELLED]:
        process_upload_batch_job.delay(upload.pk)
    else:
        upload.refresh_progress()
        update_upload_note(upload)

    return processed_rows
