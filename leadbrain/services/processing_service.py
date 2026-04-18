import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from leadbrain.models import LeadBrainCompany, LeadBrainUpload, LeadBrainWorker
from leadbrain.services.classification_service import classify_company
from leadbrain.services.research_service import research_company


logger = logging.getLogger(__name__)
STALE_PROCESSING_MINUTES = 30


def truncate_url(value: str) -> str:
    return (value or "")[:200]


def update_upload_note(upload: LeadBrainUpload) -> None:
    if upload.status == LeadBrainUpload.STATUS_PROCESSING:
        upload.status_note = "Background batch analysis is running."
    elif upload.status == LeadBrainUpload.STATUS_COMPLETE:
        upload.status_note = "Background batch analysis finished successfully."
    elif upload.status == LeadBrainUpload.STATUS_PARTIAL:
        upload.status_note = "Background batch analysis finished with some failed rows."
    elif upload.status == LeadBrainUpload.STATUS_FAILED and not upload.status_note:
        upload.status_note = "Background batch analysis did not complete."
    upload.save(update_fields=["status_note", "updated_at"])


def candidate_upload_queryset():
    return LeadBrainUpload.objects.filter(
        status__in=[
            LeadBrainUpload.STATUS_PENDING,
            LeadBrainUpload.STATUS_PROCESSING,
            LeadBrainUpload.STATUS_PARTIAL,
            LeadBrainUpload.STATUS_FAILED,
        ]
    ).order_by("uploaded_at", "id")


def update_worker_heartbeat(worker: LeadBrainWorker | None, **changes) -> None:
    if not worker:
        return
    payload = {"heartbeat_at": timezone.now()}
    payload.update(changes)
    for field, value in payload.items():
        setattr(worker, field, value)
    update_fields = list(payload.keys()) + ["updated_at"]
    worker.save(update_fields=update_fields)


def mark_stale_batch_rows_pending(upload: LeadBrainUpload) -> None:
    stale_cutoff = timezone.now() - timedelta(minutes=STALE_PROCESSING_MINUTES)
    stale_rows = upload.companies.filter(
        research_status=LeadBrainCompany.STATUS_PROCESSING,
        updated_at__lt=stale_cutoff,
    )
    if stale_rows.exists():
        stale_rows.update(
            research_status=LeadBrainCompany.STATUS_PENDING,
            research_error="Research was restarted after an interrupted batch.",
        )


def select_batch_ids(upload: LeadBrainUpload, batch_size: int) -> list[int]:
    mark_stale_batch_rows_pending(upload)
    return list(
        upload.companies.filter(
            research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_FAILED]
        )
        .order_by("row_number", "id")
        .values_list("id", flat=True)[:batch_size]
    )


def claim_batch(upload: LeadBrainUpload, batch_size: int, worker: LeadBrainWorker | None = None) -> list[int]:
    batch_ids = select_batch_ids(upload, batch_size)
    if not batch_ids:
        upload.refresh_progress()
        update_upload_note(upload)
        update_worker_heartbeat(worker, status=LeadBrainWorker.STATUS_IDLE, current_upload=None)
        return []

    LeadBrainCompany.objects.filter(id__in=batch_ids).update(
        research_status=LeadBrainCompany.STATUS_PROCESSING,
        research_error="",
    )
    upload.refresh_progress()
    update_upload_note(upload)
    update_worker_heartbeat(
        worker,
        status=LeadBrainWorker.STATUS_RUNNING,
        current_upload=upload,
        last_error="",
    )
    return batch_ids


def process_company(company: LeadBrainCompany) -> bool:
    try:
        research_data = research_company(company)
        classification = classify_company(company, research_data)

        company.website = truncate_url(company.website or research_data.get("official_website_found", ""))
        company.email = company.email or research_data.get("public_email_found", "")
        company.phone = company.phone or research_data.get("public_phone_found", "")
        company.linkedin_url = truncate_url(research_data.get("linkedin_url_found", ""))
        company.best_contact_name = research_data.get("possible_contact_name", "")
        company.best_contact_title = classification.get("best_contact_title", "")
        company.business_type = classification.get("business_type", "")
        company.fit_label = classification.get("fit_label", "")
        company.fit_score = classification.get("fit_score", 0)
        company.ai_summary = classification.get("ai_summary", "")
        company.fit_reason = classification.get("fit_reason", "")
        company.suggested_action = classification.get("suggested_action", "")
        company.research_json = research_data
        company.research_status = LeadBrainCompany.STATUS_COMPLETE
        company.research_error = ""
        company.processed_at = timezone.now()
        company.save()
        return True
    except Exception as exc:
        logger.exception("leadbrain research failed for company %s", company.pk)
        company.research_status = LeadBrainCompany.STATUS_FAILED
        company.research_error = str(exc)[:2000]
        company.processed_at = timezone.now()
        company.fit_label = ""
        company.fit_score = 0
        company.ai_summary = "Research could not be completed for this row."
        company.fit_reason = "Partial data was saved, but the row needs manual review."
        company.suggested_action = "Run Research"
        company.research_json = {
            "website_status": "failed",
            "official_website_found": "",
            "linkedin_url_found": "",
            "public_email_found": "",
            "public_phone_found": "",
            "business_description": "",
            "apparel_signals": [],
            "search_summary": "",
            "possible_contact_name": "",
            "possible_contact_title": "",
            "confidence_notes": f"Row processing error: {exc}",
        }
        company.save()
        return False


def process_upload_batch(
    upload: LeadBrainUpload,
    *,
    batch_size: int,
    worker: LeadBrainWorker | None = None,
) -> int:
    batch_ids = claim_batch(upload, batch_size, worker=worker)
    if not batch_ids:
        return 0

    processed_rows = 0
    for company in LeadBrainCompany.objects.filter(id__in=batch_ids).order_by("row_number", "id"):
        process_company(company)
        processed_rows += 1

    upload.refresh_progress()
    update_upload_note(upload)
    if worker:
        update_worker_heartbeat(
            worker,
            status=LeadBrainWorker.STATUS_RUNNING,
            current_upload=upload if upload.status == LeadBrainUpload.STATUS_PROCESSING else None,
            processed_batches=worker.processed_batches + 1,
            processed_rows=worker.processed_rows + processed_rows,
        )
    return processed_rows


def select_next_upload(upload_id: int | None = None) -> LeadBrainUpload | None:
    uploads = candidate_upload_queryset()
    if upload_id:
        uploads = uploads.filter(pk=upload_id)
    uploads = uploads.filter(
        Q(companies__research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_FAILED])
        | Q(companies__research_status=LeadBrainCompany.STATUS_PROCESSING)
    ).distinct()
    return uploads.first()

