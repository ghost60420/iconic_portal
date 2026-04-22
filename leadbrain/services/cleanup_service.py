from dataclasses import dataclass
from typing import Iterable

from django.utils import timezone

from leadbrain.models import LeadBrainCompany, LeadBrainUpload


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _website_key(value: str) -> str:
    website = _text(value).lower()
    if not website:
        return ""
    for prefix in ("https://", "http://"):
        if website.startswith(prefix):
            website = website[len(prefix) :]
            break
    if website.startswith("www."):
        website = website[4:]
    return website.rstrip("/")


def _company_rank(company: LeadBrainCompany):
    return (
        1 if company.moved_to_leads else 0,
        1 if company.research_status == LeadBrainCompany.STATUS_COMPLETE else 0,
        company.fit_score or 0,
        1 if company.reviewed else 0,
        company.updated_at,
        -company.pk,
    )


@dataclass
class CleanupResult:
    failed_uploads_found: int
    failed_uploads_archived: int
    duplicate_groups_found: int
    duplicate_rows_found: int
    duplicate_rows_archived: int
    kept_company_ids: list[int]
    archived_company_ids: list[int]


def _archive_upload(upload: LeadBrainUpload):
    upload.is_active = False
    upload.inactive_at = timezone.now()
    upload.inactive_reason = "Archived by Lead Brain cleanup because upload status is failed."
    upload.save(update_fields=["is_active", "inactive_at", "inactive_reason", "updated_at"])


def _archive_company(company: LeadBrainCompany, keeper: LeadBrainCompany):
    company.is_active = False
    company.duplicate_of = keeper
    company.inactive_at = timezone.now()
    company.inactive_reason = f"Archived by Lead Brain cleanup. Duplicate website kept as company #{keeper.pk}."
    company.save(update_fields=["is_active", "duplicate_of", "inactive_at", "inactive_reason", "updated_at"])


def _active_failed_uploads():
    return LeadBrainUpload.objects.filter(status=LeadBrainUpload.STATUS_FAILED, is_active=True).order_by("uploaded_at", "id")


def _active_company_groups_by_website() -> dict[str, list[LeadBrainCompany]]:
    groups: dict[str, list[LeadBrainCompany]] = {}
    queryset = LeadBrainCompany.objects.filter(is_active=True).exclude(website="")
    for company in queryset.select_related("upload", "duplicate_of", "moved_to_lead").order_by("id"):
        key = _website_key(company.website)
        if not key:
            continue
        groups.setdefault(key, []).append(company)
    return {key: rows for key, rows in groups.items() if len(rows) > 1}


def cleanup_leadbrain_data(*, apply_changes: bool) -> CleanupResult:
    failed_uploads = list(_active_failed_uploads())
    duplicate_groups = _active_company_groups_by_website()

    kept_company_ids: list[int] = []
    archived_company_ids: list[int] = []

    if apply_changes:
        for upload in failed_uploads:
            _archive_upload(upload)

        for companies in duplicate_groups.values():
            keeper = sorted(companies, key=_company_rank, reverse=True)[0]
            kept_company_ids.append(keeper.pk)
            for company in companies:
                if company.pk == keeper.pk:
                    continue
                _archive_company(company, keeper)
                archived_company_ids.append(company.pk)
    else:
        for companies in duplicate_groups.values():
            keeper = sorted(companies, key=_company_rank, reverse=True)[0]
            kept_company_ids.append(keeper.pk)
            archived_company_ids.extend(company.pk for company in companies if company.pk != keeper.pk)

    return CleanupResult(
        failed_uploads_found=len(failed_uploads),
        failed_uploads_archived=len(failed_uploads) if apply_changes else 0,
        duplicate_groups_found=len(duplicate_groups),
        duplicate_rows_found=sum(max(0, len(rows) - 1) for rows in duplicate_groups.values()),
        duplicate_rows_archived=len(archived_company_ids) if apply_changes else 0,
        kept_company_ids=kept_company_ids[:50],
        archived_company_ids=archived_company_ids[:50],
    )
