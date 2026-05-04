import csv
import logging
from dataclasses import dataclass
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from leadbrain.models import (
    LeadBrainCompany,
    LeadBrainDiscoveryCandidate,
    LeadBrainDiscoveryJob,
    LeadBrainDiscoveryRun,
    LeadBrainUpload,
)
from leadbrain.services.classification_service import classify_company
from leadbrain.services.matching import find_matching_lead
from leadbrain.services.research_service import research_company, search_query_results
from leadbrain.services.shopify_directory import (
    SHOPIFY_DIRECTORY_SOURCE,
    SHOPIFY_DIRECTORY_SOURCE_DETAIL,
    build_shopify_directory_queries,
    candidate_match_key,
    normalize_candidate_website,
    query_countries,
    source_detail_label,
)


logger = logging.getLogger(__name__)

DISCOVERY_SAVE_MIN_SCORE = 65
DISCOVERY_STRONG_MIN_SCORE = 80
DISCOVERY_MIN_RESULTS = 10
DISCOVERY_MAX_RESULTS = 50
DISCOVERY_MAX_JOBS_PER_DAY = 2
DISCOVERY_MAX_ACTIVE_JOBS = 1
DISCOVERY_DEFAULT_BATCH_SIZE = 10
DISCOVERY_QUERY_LIMIT = 10

SHOPIFY_DIRECTORY_EXCLUDED_HOSTS = {
    "shopify.com",
    "www.shopify.com",
    "help.shopify.com",
    "community.shopify.com",
    "apps.shopify.com",
    "themes.shopify.com",
    "partners.shopify.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "yellowpages.ca",
    "www.yellowpages.ca",
    "yelp.com",
    "www.yelp.com",
    "mapquest.com",
    "www.mapquest.com",
    "canada411.ca",
    "www.canada411.ca",
    "builtwith.com",
    "www.builtwith.com",
}
SHOPIFY_DIRECTORY_EXCLUDED_TEXT_TERMS = [
    "best shopify stores",
    "inspire your own",
    "shopify theme",
    "shopify themes",
    "shopify app",
    "shopify apps",
    "shopify partners",
    "shopify experts",
    "shopify agency",
    "shopify agencies",
    "developer",
    "development agency",
    "how to start",
    "guide to",
]


NICHE_LABELS = {
    LeadBrainDiscoveryJob.NICHE_STREETWEAR: "streetwear",
    LeadBrainDiscoveryJob.NICHE_ACTIVEWEAR: "activewear",
    LeadBrainDiscoveryJob.NICHE_KIDSWEAR: "kidswear",
    LeadBrainDiscoveryJob.NICHE_FASHION: "fashion",
    LeadBrainDiscoveryJob.NICHE_SWIMWEAR: "swimwear",
    LeadBrainDiscoveryJob.NICHE_HOODIES: "hoodies",
    LeadBrainDiscoveryJob.NICHE_TSHIRTS: "t shirts",
    LeadBrainDiscoveryJob.NICHE_ECOMMERCE: "ecommerce apparel",
    LeadBrainDiscoveryJob.NICHE_BOUTIQUE: "boutique fashion",
    LeadBrainDiscoveryJob.NICHE_PRIVATE_LABEL: "private label apparel",
    LeadBrainDiscoveryJob.NICHE_UNIFORMS: "uniforms",
    LeadBrainDiscoveryJob.NICHE_MERCH: "merch",
}


@dataclass
class DiscoveryRunResult:
    results_found: int
    candidates_saved: int
    duplicates_skipped: int
    weak_skipped: int
    failed_candidates: int


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _website_key(value):
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


def _company_name_key(value):
    return _text(value).lower()


def _niche_label(niche: str) -> str:
    return NICHE_LABELS.get(niche, _text(niche).replace("_", " "))


def _clean_company_name(title: str) -> str:
    value = _text(title)
    if not value:
        return ""
    for separator in (" | ", " - ", " – ", " — ", " :: "):
        if separator in value:
            value = value.split(separator, 1)[0]
            break
    return value[:255]


def normalized_max_results(value: int) -> int:
    return max(DISCOVERY_MIN_RESULTS, min(int(value or DISCOVERY_MIN_RESULTS), DISCOVERY_MAX_RESULTS))


def normalized_min_fit_score(value: int) -> int:
    return max(DISCOVERY_SAVE_MIN_SCORE, min(int(value or DISCOVERY_SAVE_MIN_SCORE), 100))


def can_queue_discovery_job(*, user=None) -> tuple[bool, str]:
    if user:
        today = timezone.localdate()
        daily_runs = LeadBrainDiscoveryRun.objects.filter(
            job__created_by=user,
            created_at__date=today,
        ).count()
        if daily_runs >= DISCOVERY_MAX_JOBS_PER_DAY:
            return False, f"Daily discovery job limit reached ({DISCOVERY_MAX_JOBS_PER_DAY} per day)."

    active_jobs = LeadBrainDiscoveryRun.objects.filter(
        status__in=LeadBrainDiscoveryRun.ACTIVE_STATUSES
    ).count()
    if active_jobs >= DISCOVERY_MAX_ACTIVE_JOBS:
        return False, "Lead Brain discovery is busy right now. Wait for the active run to finish."

    return True, ""


def get_due_discovery_jobs(*, now=None):
    now = now or timezone.now()
    return LeadBrainDiscoveryJob.objects.filter(
        is_active=True,
        is_paused=False,
        schedule_type__in=[LeadBrainDiscoveryJob.SCHEDULE_DAILY, LeadBrainDiscoveryJob.SCHEDULE_WEEKLY],
        next_run_at__isnull=False,
        next_run_at__lte=now,
    ).exclude(
        runs__status__in=LeadBrainDiscoveryRun.ACTIVE_STATUSES
    ).order_by("next_run_at", "id")


def _find_matching_company(*, website="", email="", company_name=""):
    website_key = _website_key(website)
    email_key = _text(email).lower()
    company_name_key = _company_name_key(company_name)
    filters = Q()
    if website_key:
        filters |= Q(website__icontains=website_key)
    if email_key:
        filters |= Q(email__iexact=email_key)
    if not filters:
        return None, ""

    queryset = LeadBrainCompany.objects.filter(is_active=True).filter(filters).order_by("id")
    for record in queryset:
        record_website_key = _website_key(record.website)
        record_email_key = _text(record.email).lower()
        record_company_name_key = _company_name_key(record.company_name)
        if website_key and record_website_key == website_key:
            return record, "website exact match"
        if email_key and record_email_key == email_key:
            return record, "email exact match"
        if company_name_key and website_key and record_company_name_key == company_name_key and record_website_key == website_key:
            return record, "company name plus website"
        if company_name_key and email_key and record_company_name_key == company_name_key and record_email_key == email_key:
            return record, "company name plus email"
    return None, ""


def _discovery_band(score: int) -> str:
    if score >= DISCOVERY_STRONG_MIN_SCORE:
        return "strong_fit"
    if score >= DISCOVERY_SAVE_MIN_SCORE:
        return "possible_fit"
    return "weak_fit"


def _looks_apparel_related(research_data, classification) -> bool:
    if research_data.get("apparel_signals"):
        return True
    business_type = _text(classification.get("business_type", "")).lower()
    return any(
        token in business_type
        for token in ["apparel", "fashion", "clothing", "streetwear", "activewear", "kidswear", "uniform", "merch"]
    )


def _shopify_directory_domain(url: str) -> str:
    key = _website_key(url)
    return key.split("/", 1)[0]


def _looks_like_shopify_clothing_store(candidate: LeadBrainDiscoveryCandidate, research_data: dict, classification: dict) -> tuple[bool, str]:
    if candidate.source_type != SHOPIFY_DIRECTORY_SOURCE:
        return True, ""

    host = _shopify_directory_domain(_text(research_data.get("official_website_found") or candidate.website))
    combined_text = " ".join(
        [
            _text(candidate.company_name),
            _text(research_data.get("business_description")),
            _text(research_data.get("search_summary")),
            _text(research_data.get("confidence_notes")),
        ]
    ).lower()

    if host in SHOPIFY_DIRECTORY_EXCLUDED_HOSTS:
        return False, "Skipped because the result was a platform, directory, or search domain."
    if host.endswith(".shopify.com") and not host.endswith(".myshopify.com"):
        return False, "Skipped because the result looked like a Shopify platform page, not a brand storefront."
    if any(term in combined_text for term in SHOPIFY_DIRECTORY_EXCLUDED_TEXT_TERMS):
        return False, "Skipped because the result looked like an article, agency, or informational page."
    if not research_data.get("shopify_signal_found"):
        return False, "Skipped because no reliable Shopify storefront signal was found."
    if not research_data.get("product_or_collection_found"):
        return False, "Skipped because no public product or collection pages were confirmed."
    if not _looks_apparel_related(research_data, classification):
        return False, "Skipped because the storefront did not look apparel-focused."
    return True, ""


def suggest_products_to_pitch(*, research_data, classification) -> list[str]:
    business_type = _text(classification.get("business_type", "")).lower()
    signals = {signal.lower() for signal in (research_data.get("apparel_signals") or [])}
    if "uniform" in signals or "uniform" in business_type:
        return ["Polos", "Work Shirts", "Outerwear"]
    if "activewear" in signals or "sportswear" in signals:
        return ["Performance Tees", "Joggers", "Hoodies"]
    if "kidswear" in signals:
        return ["Kids Tees", "Matching Sets", "Fleece"]
    if "merch" in signals or "merch" in business_type:
        return ["Graphic Tees", "Caps", "Hoodies"]
    if "streetwear" in signals:
        return ["Heavyweight Tees", "Fleece Hoodies", "Varsity Jackets"]
    if "private label" in signals or "manufacturer" in business_type:
        return ["Private Label Tees", "Polos", "Cut and Sew Fleece"]
    return ["Tees", "Hoodies", "Polos"]


def _build_query_plan(job: LeadBrainDiscoveryJob) -> list[dict]:
    queries = []
    for country in job.effective_countries:
        search_countries = query_countries(country) if country == LeadBrainDiscoveryJob.COUNTRY_NORTH_AMERICA else [country]
        for niche in job.effective_niches:
            niche_label = _niche_label(niche)
            for source in job.effective_source_types:
                for search_country in search_countries:
                    if source == LeadBrainDiscoveryJob.SOURCE_DIRECTORIES:
                        domains = "site:yellowpages.ca OR site:yelp.com OR site:canada411.ca OR site:mapquest.com"
                        query_list = [
                            f"{domains} {search_country} {niche_label} apparel",
                            f"{domains} {search_country} {niche_label} clothing brand",
                            f"{domains} {search_country} {niche_label} fashion store",
                        ]
                    elif source == LeadBrainDiscoveryJob.SOURCE_SHOPIFY:
                        query_list = [
                            f'"powered by shopify" {search_country} {niche_label} apparel',
                            f'"powered by Shopify" {search_country} {niche_label} fashion brand',
                            f"site:myshopify.com {search_country} {niche_label} clothing",
                        ]
                    elif source == SHOPIFY_DIRECTORY_SOURCE:
                        query_list = build_shopify_directory_queries(search_country, niche_label)
                    else:
                        query_list = [
                            f"{search_country} {niche_label} apparel brand",
                            f"{search_country} {niche_label} clothing brand",
                            f"{search_country} {niche_label} fashion company",
                        ]
                    for query in query_list:
                        queries.append(
                            {
                                "query": query,
                                "source_type": source,
                                "country": search_country,
                                "job_country": country,
                                "niche": niche,
                            }
                        )
    return queries


def _build_discovery_upload(run: LeadBrainDiscoveryRun) -> LeadBrainUpload:
    job = run.job
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"discovery_run_{run.pk}_{timestamp}.csv"
    upload = LeadBrainUpload(
        uploaded_by=job.created_by,
        file_name=file_name,
        status=LeadBrainUpload.STATUS_QUEUED,
        status_note="Discovery run queued.",
    )
    content = ContentFile("company_name,website,country,source_type,niche\n")
    upload.file.save(file_name, content, save=False)
    upload.file_size = upload.file.size if upload.file else 0
    upload.save()
    run.upload = upload
    run.save(update_fields=["upload", "updated_at"])
    job.upload = upload
    job.save(update_fields=["upload", "updated_at"])
    return upload


def _append_upload_snapshot(upload: LeadBrainUpload, saved_rows: list[dict]) -> None:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["company_name", "website", "country", "source_type", "niche"])
    for row in saved_rows:
        raw = row.get("raw_row_json", {}) or {}
        writer.writerow(
            [
                row.get("company_name", ""),
                row.get("website", ""),
                row.get("country", ""),
                raw.get("discovery_source_type") or raw.get("source_type", ""),
                raw.get("discovery_niche") or raw.get("niche", ""),
            ]
        )
    upload.file.save(upload.file_name or f"discovery_{upload.pk}.csv", ContentFile(buffer.getvalue()), save=False)
    upload.file_size = upload.file.size if upload.file else 0


def _job_source_label(job: LeadBrainDiscoveryJob, source_type: str) -> str:
    return dict(LeadBrainDiscoveryJob.SOURCE_CHOICES).get(source_type, source_detail_label(source_type))


def _discovery_source_detail(candidate: LeadBrainDiscoveryCandidate) -> str:
    if candidate.source_type == SHOPIFY_DIRECTORY_SOURCE:
        return SHOPIFY_DIRECTORY_SOURCE_DETAIL
    parts = [_job_source_label(candidate.run.job, candidate.source_type or candidate.run.job.source_type)]
    if candidate.country:
        parts.append(candidate.country)
    if candidate.niche:
        parts.append(_niche_label(candidate.niche))
    return " / ".join([part for part in parts if part])[:255]


def _saved_rows_for_run(run: LeadBrainDiscoveryRun) -> list[dict]:
    rows = []
    queryset = LeadBrainCompany.objects.filter(discovery_run=run).order_by("id")
    for company in queryset:
        rows.append(
            {
                "company_name": company.company_name,
                "website": company.website,
                "country": company.country,
                "raw_row_json": company.raw_row_json or {},
            }
        )
    return rows


def _duplicate_examples_for_run(run: LeadBrainDiscoveryRun) -> list[str]:
    return list(
        run.candidates.filter(discovery_status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE)
        .exclude(skip_reason="")
        .order_by("id")
        .values_list("skip_reason", flat=True)[:5]
    )


def _saved_examples_for_run(run: LeadBrainDiscoveryRun) -> list[dict]:
    queryset = run.candidates.filter(
        discovery_status=LeadBrainDiscoveryCandidate.STATUS_SAVED
    ).order_by("-fit_score", "id")[:5]
    return [
        {
            "company_name": item.company_name,
            "website": item.website,
            "fit_score": item.fit_score,
        }
        for item in queryset
    ]


def _sync_run_metrics(run: LeadBrainDiscoveryRun) -> LeadBrainDiscoveryRun:
    stats = run.candidates.aggregate(
        total=Count("id"),
        saved=Count("id", filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_SAVED)),
        duplicate=Count("id", filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE)),
        weak=Count("id", filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_WEAK)),
        failed=Count("id", filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_FAILED)),
        strong=Count(
            "id",
            filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_SAVED, fit_score__gte=DISCOVERY_STRONG_MIN_SCORE),
        ),
        processing=Count("id", filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_PROCESSING)),
        queued=Count("id", filter=Q(discovery_status=LeadBrainDiscoveryCandidate.STATUS_QUEUED)),
    )

    run.total_candidates_found = stats["total"] or 0
    run.total_candidates_saved = stats["saved"] or 0
    run.total_duplicates_skipped = stats["duplicate"] or 0
    run.total_weak_skipped = stats["weak"] or 0
    run.total_failed = stats["failed"] or 0
    run.results_found = run.total_candidates_found
    run.candidates_saved = run.total_candidates_saved
    run.duplicates_skipped = run.total_duplicates_skipped
    run.weak_skipped = run.total_weak_skipped
    run.failed_candidates = run.total_failed
    run.strong_fits_found = stats["strong"] or 0
    run.save(
        update_fields=[
            "total_candidates_found",
            "total_candidates_saved",
            "total_duplicates_skipped",
            "total_weak_skipped",
            "total_failed",
            "results_found",
            "candidates_saved",
            "duplicates_skipped",
            "weak_skipped",
            "failed_candidates",
            "strong_fits_found",
            "updated_at",
        ]
    )
    return run


def _sync_upload_from_run(run: LeadBrainDiscoveryRun) -> None:
    if not run.upload_id:
        return
    upload = run.upload
    total = run.total_candidates_found
    pending = run.candidates.filter(discovery_status=LeadBrainDiscoveryCandidate.STATUS_QUEUED).count()
    processing = run.candidates.filter(discovery_status=LeadBrainDiscoveryCandidate.STATUS_PROCESSING).count()
    processed = max(0, total - pending - processing)
    progress_percent = min(100, int((processed * 100) / total)) if total else 0

    upload.source_row_count = total
    upload.row_count = total
    upload.total_rows = total
    upload.imported_rows = run.total_candidates_saved
    upload.skipped_duplicate_rows = run.total_duplicates_skipped
    upload.invalid_rows = 0
    upload.blank_rows = 0
    upload.pending_rows = pending
    upload.processing_rows = processing
    upload.completed_rows = processed
    upload.failed_rows = run.total_failed
    upload.progress_percent = progress_percent
    upload.duplicate_row_examples_json = _duplicate_examples_for_run(run)

    if run.status == LeadBrainDiscoveryJob.STATUS_QUEUED:
        upload.status = LeadBrainUpload.STATUS_QUEUED
    elif run.status == LeadBrainDiscoveryJob.STATUS_PROCESSING:
        upload.status = LeadBrainUpload.STATUS_PROCESSING
    elif run.status == LeadBrainDiscoveryJob.STATUS_PARTIAL:
        upload.status = LeadBrainUpload.STATUS_PARTIAL
    elif run.status == LeadBrainDiscoveryJob.STATUS_FAILED:
        upload.status = LeadBrainUpload.STATUS_FAILED
    elif run.status == LeadBrainDiscoveryJob.STATUS_CANCELLED:
        upload.status = LeadBrainUpload.STATUS_CANCELLED
    else:
        upload.status = LeadBrainUpload.STATUS_COMPLETE
    upload.status_note = run.status_note[:2000]

    if run.completed_at:
        _append_upload_snapshot(upload, _saved_rows_for_run(run))

    upload.save(
        update_fields=[
            "file",
            "file_size",
            "source_row_count",
            "row_count",
            "total_rows",
            "imported_rows",
            "skipped_duplicate_rows",
            "invalid_rows",
            "blank_rows",
            "pending_rows",
            "processing_rows",
            "completed_rows",
            "failed_rows",
            "progress_percent",
            "duplicate_row_examples_json",
            "status",
            "status_note",
            "updated_at",
        ]
    )


def _sync_job_from_run(run: LeadBrainDiscoveryRun, *, finalized=False) -> None:
    job = run.job
    job.results_found = run.total_candidates_found
    job.candidates_saved = run.total_candidates_saved
    job.duplicates_skipped = run.total_duplicates_skipped
    job.weak_skipped = run.total_weak_skipped
    job.failed_candidates = run.total_failed
    job.duplicate_examples_json = _duplicate_examples_for_run(run)
    job.saved_examples_json = _saved_examples_for_run(run)
    job.status = run.status
    job.status_note = run.status_note[:2000]
    if finalized:
        now = run.completed_at or timezone.now()
        if not job.last_run_at or (run.started_at and run.started_at >= job.last_run_at):
            job.total_runs = (job.total_runs or 0) + 1
            job.total_leads_found = (job.total_leads_found or 0) + run.total_candidates_saved
            job.total_strong_fits = (job.total_strong_fits or 0) + run.strong_fits_found
        job.last_run_at = now
        job.next_run_at = job.compute_next_run_at(reference=now)
    job.save(
        update_fields=[
            "results_found",
            "candidates_saved",
            "duplicates_skipped",
            "weak_skipped",
            "failed_candidates",
            "sample_results_json",
            "duplicate_examples_json",
            "saved_examples_json",
            "status",
            "status_note",
            "total_runs",
            "total_leads_found",
            "total_strong_fits",
            "last_run_at",
            "next_run_at",
            "updated_at",
        ]
    )


def queue_manual_discovery_run(job: LeadBrainDiscoveryJob, *, created_by=None) -> LeadBrainDiscoveryRun:
    with transaction.atomic():
        job = LeadBrainDiscoveryJob.objects.select_for_update().get(pk=job.pk)
        active_run = job.runs.filter(status__in=LeadBrainDiscoveryRun.ACTIVE_STATUSES).order_by("id").first()
        if active_run:
            return active_run
        run = LeadBrainDiscoveryRun.objects.create(
            job=job,
            status=LeadBrainDiscoveryJob.STATUS_QUEUED,
            status_note="Discovery run queued for background processing.",
        )
        job.status = LeadBrainDiscoveryJob.STATUS_QUEUED
        job.status_note = "Discovery run queued for background processing."
        job.save(update_fields=["status", "status_note", "updated_at"])
        return run


def schedule_due_discovery_runs(*, now=None, limit=None) -> list[LeadBrainDiscoveryRun]:
    now = now or timezone.now()
    queryset = get_due_discovery_jobs(now=now)
    if limit:
        queryset = queryset[:limit]
    runs = []
    for job in queryset:
        with transaction.atomic():
            locked_job = LeadBrainDiscoveryJob.objects.select_for_update().get(pk=job.pk)
            if locked_job.runs.filter(status__in=LeadBrainDiscoveryRun.ACTIVE_STATUSES).exists():
                continue
            daily_limit = max(1, min(int(locked_job.max_runs_per_day or 1), DISCOVERY_MAX_JOBS_PER_DAY))
            runs_today = locked_job.runs.filter(created_at__date=timezone.localdate(now)).count()
            if runs_today >= daily_limit:
                if locked_job.schedule_type == LeadBrainDiscoveryJob.SCHEDULE_WEEKLY:
                    locked_job.next_run_at = locked_job.compute_next_run_at(reference=now + timedelta(days=7))
                else:
                    locked_job.next_run_at = locked_job.compute_next_run_at(reference=now + timedelta(days=1))
                locked_job.status_note = "Discovery daily run limit reached. Next run was deferred."
                locked_job.save(update_fields=["next_run_at", "status_note", "updated_at"])
                continue
            run = LeadBrainDiscoveryRun.objects.create(
                job=locked_job,
                status=LeadBrainDiscoveryJob.STATUS_QUEUED,
                status_note="Scheduled discovery run queued for background processing.",
            )
            locked_job.status = LeadBrainDiscoveryJob.STATUS_QUEUED
            locked_job.status_note = "Scheduled discovery run queued for background processing."
            locked_job.next_run_at = locked_job.compute_next_run_at(reference=now + timedelta(seconds=1))
            locked_job.save(update_fields=["status", "status_note", "next_run_at", "updated_at"])
            runs.append(run)
    return runs


def initialize_discovery_run(run: LeadBrainDiscoveryRun) -> LeadBrainDiscoveryRun:
    run = LeadBrainDiscoveryRun.objects.select_related("job", "upload").get(pk=run.pk)
    if run.queries_json and run.candidates.exists():
        return run

    if not run.upload_id:
        _build_discovery_upload(run)
        run.refresh_from_db()

    query_plan = _build_query_plan(run.job)
    candidate_budget = run.job.effective_max_results_per_run
    seen_urls = set()
    candidates = []
    duplicate_examples = []
    sample_results = []
    error_messages = []

    for query_spec in query_plan:
        if len(candidates) >= candidate_budget:
            break
        try:
            query_payload = search_query_results(query_spec["query"], limit=DISCOVERY_QUERY_LIMIT)
        except Exception as exc:
            logger.exception("leadbrain discovery query failed for run %s", run.pk)
            error_messages.append(str(exc))
            continue

        for result in query_payload.get("results", []):
            if len(candidates) >= candidate_budget:
                break
            source_url = _text(result.get("url", ""))[:200]
            source_type = query_spec["source_type"]
            candidate_website = (
                normalize_candidate_website(source_url)
                if source_type in {LeadBrainDiscoveryJob.SOURCE_SHOPIFY, SHOPIFY_DIRECTORY_SOURCE}
                else source_url
            )
            website_key = (
                candidate_match_key(source_url)
                if source_type in {LeadBrainDiscoveryJob.SOURCE_SHOPIFY, SHOPIFY_DIRECTORY_SOURCE}
                else _website_key(source_url)
            )
            company_name = _clean_company_name(result.get("title", ""))
            if not website_key:
                continue
            if website_key in seen_urls:
                candidates.append(
                    LeadBrainDiscoveryCandidate(
                        run=run,
                        company_name=company_name,
                        website=candidate_website,
                        source_type=source_type,
                        source_url=source_url,
                        country=query_spec["country"],
                        niche=query_spec["niche"],
                        discovery_status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE,
                        skip_reason=(
                            "Duplicate domain in the same discovery run."
                            if source_type == SHOPIFY_DIRECTORY_SOURCE
                            else "Duplicate URL in the same discovery run."
                        ),
                    )
                )
                if len(duplicate_examples) < 5:
                    duplicate_examples.append(
                        f"{company_name or source_url} skipped by same-run "
                        f"{'domain' if source_type == SHOPIFY_DIRECTORY_SOURCE else 'URL'} dedupe."
                    )
                continue

            seen_urls.add(website_key)
            existing_company, company_rule = _find_matching_company(
                website=candidate_website,
                company_name=company_name,
            )
            duplicate_lead, lead_rule = find_matching_lead(website=candidate_website)

            discovery_status = LeadBrainDiscoveryCandidate.STATUS_QUEUED
            skip_reason = ""
            if existing_company:
                discovery_status = LeadBrainDiscoveryCandidate.STATUS_DUPLICATE
                skip_reason = f"Skipped by existing Lead Brain {company_rule}."
            elif duplicate_lead:
                discovery_status = LeadBrainDiscoveryCandidate.STATUS_DUPLICATE
                skip_reason = f"Skipped by existing Lead {lead_rule}."

            candidates.append(
                LeadBrainDiscoveryCandidate(
                    run=run,
                    company_name=company_name,
                    website=candidate_website,
                    source_type=source_type,
                    source_url=source_url,
                    country=query_spec["country"],
                    niche=query_spec["niche"],
                    discovery_status=discovery_status,
                    skip_reason=skip_reason[:255],
                )
            )
            if skip_reason and len(duplicate_examples) < 5:
                duplicate_examples.append(f"{company_name or source_url} {skip_reason}")
            if len(sample_results) < 5:
                sample_results.append(
                    {
                        "title": result.get("title", ""),
                        "url": candidate_website,
                        "source_type": source_type,
                        "country": query_spec["country"],
                        "niche": query_spec["niche"],
                        "query": query_spec["query"],
                    }
                )

    if candidates:
        LeadBrainDiscoveryCandidate.objects.bulk_create(candidates, batch_size=100)

    run.queries_json = query_plan
    run.status = LeadBrainDiscoveryJob.STATUS_PROCESSING
    run.started_at = run.started_at or timezone.now()
    run.error_message = "\n".join(error_messages[:10])[:4000]
    run.status_note = "Discovery candidates collected. Background scoring is running."
    run.save(update_fields=["queries_json", "status", "started_at", "error_message", "status_note", "updated_at"])

    run.job.query_plan_json = query_plan
    run.job.sample_results_json = sample_results
    run.job.duplicate_examples_json = duplicate_examples
    run.job.status = LeadBrainDiscoveryJob.STATUS_PROCESSING
    run.job.status_note = "Discovery candidates collected. Background scoring is running."
    run.job.save(
        update_fields=["query_plan_json", "sample_results_json", "duplicate_examples_json", "status", "status_note", "updated_at"]
    )

    _sync_run_metrics(run)
    _sync_upload_from_run(run)

    if not run.candidates.filter(discovery_status=LeadBrainDiscoveryCandidate.STATUS_QUEUED).exists():
        finalize_discovery_run(run)
    return run


def _claim_run(run_id=None) -> LeadBrainDiscoveryRun | None:
    with transaction.atomic():
        queryset = LeadBrainDiscoveryRun.objects.select_for_update().select_related("job", "upload")
        if run_id:
            run = queryset.filter(pk=run_id).first()
        else:
            run = queryset.filter(status__in=LeadBrainDiscoveryRun.ACTIVE_STATUSES).order_by("created_at", "id").first()
        if not run:
            return None
        if run.status == LeadBrainDiscoveryJob.STATUS_QUEUED:
            run.status = LeadBrainDiscoveryJob.STATUS_PROCESSING
            run.started_at = run.started_at or timezone.now()
            run.status_note = "Discovery run is processing in the background."
            run.save(update_fields=["status", "started_at", "status_note", "updated_at"])
            run.job.status = LeadBrainDiscoveryJob.STATUS_PROCESSING
            run.job.status_note = "Discovery run is processing in the background."
            run.job.save(update_fields=["status", "status_note", "updated_at"])
        return run


def _claim_run_candidates(run: LeadBrainDiscoveryRun, *, batch_size: int) -> list[LeadBrainDiscoveryCandidate]:
    with transaction.atomic():
        run = LeadBrainDiscoveryRun.objects.select_for_update().get(pk=run.pk)
        candidate_ids = list(
            run.candidates.filter(discovery_status=LeadBrainDiscoveryCandidate.STATUS_QUEUED)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )
        if not candidate_ids:
            return []
        now = timezone.now()
        LeadBrainDiscoveryCandidate.objects.filter(
            pk__in=candidate_ids,
            discovery_status=LeadBrainDiscoveryCandidate.STATUS_QUEUED,
        ).update(
            discovery_status=LeadBrainDiscoveryCandidate.STATUS_PROCESSING,
            updated_at=now,
        )
    return list(
        LeadBrainDiscoveryCandidate.objects.filter(pk__in=candidate_ids).select_related("run", "run__job", "run__upload")
    )


def _candidate_stub(candidate: LeadBrainDiscoveryCandidate) -> SimpleNamespace:
    return SimpleNamespace(
        company_name=candidate.company_name,
        website=candidate.website,
        email="",
        phone="",
        country=candidate.country,
        city="",
        raw_row_json={
            "leadbrain_source": "discovery",
            "source_type": candidate.source_type,
            "source_detail": _discovery_source_detail(candidate),
            "country": candidate.country,
            "requested_country": candidate.country,
            "niche": candidate.niche,
            "source_url": candidate.source_url,
        },
    )


def _saved_company_source_type(candidate: LeadBrainDiscoveryCandidate) -> str:
    if candidate.source_type == SHOPIFY_DIRECTORY_SOURCE:
        return "discovery"
    return _text(candidate.source_type or candidate.run.job.source_type)[:40]


def _apply_discovery_score_adjustments(candidate: LeadBrainDiscoveryCandidate, research_data: dict, classification: dict) -> dict:
    if candidate.source_type != SHOPIFY_DIRECTORY_SOURCE:
        return classification

    adjusted = dict(classification or {})
    score = int(adjusted.get("fit_score") or 0)
    boosts = []
    penalties = []

    if research_data.get("shopify_signal_found"):
        score += 8
        boosts.append("Shopify signal found")
    else:
        score -= 10
        penalties.append("Shopify signal missing")

    if research_data.get("product_or_collection_found"):
        score += 8
        boosts.append("product pages found")
    else:
        score -= 12
        penalties.append("no product pages found")

    apparel_signals = research_data.get("apparel_signals") or []
    if apparel_signals:
        score += min(8, len(apparel_signals) * 2)
        boosts.append("apparel keywords found")
    else:
        score -= 16
        penalties.append("no apparel signal")

    if research_data.get("north_america_signal_found"):
        score += 5
        boosts.append("North America signal found")

    if research_data.get("contact_page_found") or research_data.get("public_email_found") or research_data.get("public_phone_found"):
        score += 4
        boosts.append("contact page or contact signal found")

    if research_data.get("website_status") not in {"live", "redirect"}:
        score -= 20
        penalties.append("dead or unavailable site")

    if not _text(research_data.get("business_description")) and not _text(research_data.get("search_summary")):
        score -= 6
        penalties.append("brand positioning is unclear")

    from leadbrain.services.classification_service import map_fit_label

    score = max(0, min(100, score))
    adjusted["fit_score"] = score
    adjusted["fit_label"] = map_fit_label(score)

    fit_reason = _text(adjusted.get("fit_reason"))
    detail_parts = []
    if boosts:
        detail_parts.append("Boosts: " + ", ".join(boosts[:5]))
    if penalties:
        detail_parts.append("Reductions: " + ", ".join(penalties[:5]))
    if detail_parts:
        adjusted["fit_reason"] = " ".join(part for part in [fit_reason, " ".join(detail_parts)] if part).strip()

    ai_summary = _text(adjusted.get("ai_summary"))
    if research_data.get("shopify_signal_found"):
        adjusted["ai_summary"] = " ".join(
            part
            for part in [
                ai_summary,
                "Shopify storefront signals and apparel collection checks were confirmed from public pages.",
            ]
            if part
        ).strip()
    return adjusted


def _mark_candidate(candidate: LeadBrainDiscoveryCandidate, *, status: str, research_json=None, fit_score=0, fit_label="", skip_reason="", created_company=None, website=None):
    candidate.discovery_status = status
    candidate.research_json = research_json or {}
    candidate.fit_score = int(fit_score or 0)
    candidate.fit_label = _text(fit_label)[:20]
    candidate.skip_reason = _text(skip_reason)[:255]
    if created_company:
        candidate.created_leadbrain_company = created_company
    if website:
        candidate.website = _text(website)[:200]
    candidate.save(
        update_fields=[
            "discovery_status",
            "research_json",
            "fit_score",
            "fit_label",
            "skip_reason",
            "created_leadbrain_company",
            "website",
            "updated_at",
        ]
    )


def _save_candidate_to_leadbrain(run: LeadBrainDiscoveryRun, candidate: LeadBrainDiscoveryCandidate, research_data, classification) -> LeadBrainCompany:
    job = run.job
    score = int(classification.get("fit_score") or 0)
    source_meta = {
        "leadbrain_source": "discovery",
        "source_type": candidate.source_type or job.source_type,
        "country": candidate.country or (job.effective_countries[0] if job.effective_countries else ""),
        "niche": candidate.niche or (job.effective_niches[0] if job.effective_niches else ""),
        "source_url": candidate.source_url,
        "discovery_source_type": candidate.source_type or job.source_type,
        "discovery_country": candidate.country or (job.effective_countries[0] if job.effective_countries else ""),
        "discovery_niche": candidate.niche or (job.effective_niches[0] if job.effective_niches else ""),
        "discovery_source_detail": _discovery_source_detail(candidate),
    }
    products = suggest_products_to_pitch(research_data=research_data, classification=classification)
    research_data["discovery_tier"] = _discovery_band(score)
    research_data["suggested_products_to_pitch"] = products

    return LeadBrainCompany.objects.create(
        upload=run.upload,
        row_number=candidate.pk,
        company_name=_text(candidate.company_name)[:255],
        website=_text(research_data.get("official_website_found") or candidate.website)[:200],
        email=_text(research_data.get("public_email_found"))[:254],
        phone=_text(research_data.get("public_phone_found"))[:100],
        country=_text(candidate.country)[:100],
        city="",
        linkedin_url=_text(research_data.get("linkedin_url_found"))[:200],
        best_contact_name=_text(research_data.get("possible_contact_name"))[:255],
        best_contact_title=_text(classification.get("best_contact_title"))[:255],
        business_type=_text(classification.get("business_type"))[:255],
        fit_label=_text(classification.get("fit_label")),
        fit_score=score,
        ai_summary=_text(classification.get("ai_summary")),
        fit_reason=_text(classification.get("fit_reason")),
        suggested_action=_text(classification.get("suggested_action"))[:255],
        raw_row_json=source_meta,
        research_json=research_data,
        research_status=LeadBrainCompany.STATUS_COMPLETE,
        processed_at=timezone.now(),
        notes=(
            f"Discovery source: {_discovery_source_detail(candidate)}.\n"
            f"Suggested pitch products: {', '.join(products)}."
        ),
        source_type=_saved_company_source_type(candidate),
        source_detail=_discovery_source_detail(candidate),
        discovery_job=job,
        discovery_run=run,
    )


def process_discovery_run_batch(run: LeadBrainDiscoveryRun, *, batch_size=DISCOVERY_DEFAULT_BATCH_SIZE) -> int:
    run = LeadBrainDiscoveryRun.objects.select_related("job", "upload").get(pk=run.pk)
    if not run.queries_json or not run.candidates.exists():
        initialize_discovery_run(run)
        run.refresh_from_db()

    candidates = _claim_run_candidates(run, batch_size=batch_size)
    if not candidates:
        finalize_discovery_run(run)
        return 0

    for candidate in candidates:
        try:
            existing_company, company_rule = _find_matching_company(
                website=candidate.website,
                company_name=candidate.company_name,
            )
            duplicate_lead, lead_rule = find_matching_lead(website=candidate.website)
            if existing_company:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE,
                    skip_reason=f"Skipped by existing Lead Brain {company_rule}.",
                )
                continue
            if duplicate_lead:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE,
                    skip_reason=f"Skipped by existing Lead {lead_rule}.",
                )
                continue

            research_data = research_company(_candidate_stub(candidate))
            classification = classify_company(_candidate_stub(candidate), research_data)
            classification = _apply_discovery_score_adjustments(candidate, research_data, classification)
            website = _text(research_data.get("official_website_found") or candidate.website)[:200]
            email = _text(research_data.get("public_email_found"))

            has_active_site = research_data.get("website_status") in {"live", "redirect"} or bool(website)
            if not has_active_site:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_WEAK,
                    research_json=research_data,
                    fit_score=classification.get("fit_score", 0),
                    fit_label=classification.get("fit_label", ""),
                    skip_reason="Skipped because the website was unavailable or too weak to research.",
                )
                continue
            storefront_ok, storefront_reason = _looks_like_shopify_clothing_store(candidate, research_data, classification)
            if not storefront_ok:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_WEAK,
                    research_json=research_data,
                    fit_score=classification.get("fit_score", 0),
                    fit_label=classification.get("fit_label", ""),
                    skip_reason=storefront_reason,
                    website=website,
                )
                continue
            if candidate.run.job.apparel_only and not _looks_apparel_related(research_data, classification):
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_WEAK,
                    research_json=research_data,
                    fit_score=classification.get("fit_score", 0),
                    fit_label=classification.get("fit_label", ""),
                    skip_reason="Skipped because the business did not look apparel-focused.",
                    website=website,
                )
                continue

            existing_company, company_rule = _find_matching_company(
                website=website,
                email=email,
                company_name=candidate.company_name,
            )
            duplicate_lead, lead_rule = find_matching_lead(website=website, email=email)
            if existing_company:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE,
                    research_json=research_data,
                    fit_score=classification.get("fit_score", 0),
                    fit_label=classification.get("fit_label", ""),
                    skip_reason=f"Skipped by existing Lead Brain {company_rule}.",
                    website=website,
                )
                continue
            if duplicate_lead:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_DUPLICATE,
                    research_json=research_data,
                    fit_score=classification.get("fit_score", 0),
                    fit_label=classification.get("fit_label", ""),
                    skip_reason=f"Skipped by existing Lead {lead_rule}.",
                    website=website,
                )
                continue

            score = int(classification.get("fit_score") or 0)
            minimum_score = normalized_min_fit_score(candidate.run.job.effective_min_fit_score)
            if score < minimum_score:
                _mark_candidate(
                    candidate,
                    status=LeadBrainDiscoveryCandidate.STATUS_WEAK,
                    research_json=research_data,
                    fit_score=score,
                    fit_label=classification.get("fit_label", ""),
                    skip_reason=f"Skipped because fit score {score} is below the job minimum of {minimum_score}.",
                    website=website,
                )
                continue

            company = _save_candidate_to_leadbrain(run, candidate, research_data, classification)
            _mark_candidate(
                candidate,
                status=LeadBrainDiscoveryCandidate.STATUS_SAVED,
                research_json=research_data,
                fit_score=score,
                fit_label=classification.get("fit_label", ""),
                created_company=company,
                website=website,
            )
        except Exception as exc:
            logger.exception("leadbrain discovery candidate failed for run %s candidate %s", run.pk, candidate.pk)
            _mark_candidate(
                candidate,
                status=LeadBrainDiscoveryCandidate.STATUS_FAILED,
                skip_reason=f"Candidate processing failed: {exc}",
                website=candidate.website,
            )

    run.refresh_from_db()
    _sync_run_metrics(run)
    run.status = LeadBrainDiscoveryJob.STATUS_PROCESSING
    run.status_note = "Discovery scoring is running in small background batches."
    run.save(update_fields=["status", "status_note", "updated_at"])
    _sync_upload_from_run(run)
    _sync_job_from_run(run, finalized=False)
    finalize_discovery_run(run)
    return len(candidates)


def finalize_discovery_run(run: LeadBrainDiscoveryRun) -> LeadBrainDiscoveryRun:
    run = LeadBrainDiscoveryRun.objects.select_related("job", "upload").get(pk=run.pk)
    _sync_run_metrics(run)
    pending = run.candidates.filter(
        discovery_status__in=[LeadBrainDiscoveryCandidate.STATUS_QUEUED, LeadBrainDiscoveryCandidate.STATUS_PROCESSING]
    ).exists()
    if pending:
        return run
    if run.completed_at:
        return run

    if run.total_failed and not run.total_candidates_saved and not run.total_duplicates_skipped and not run.total_weak_skipped:
        final_status = LeadBrainDiscoveryJob.STATUS_FAILED
    elif run.total_failed and (run.total_candidates_saved or run.total_duplicates_skipped or run.total_weak_skipped):
        final_status = LeadBrainDiscoveryJob.STATUS_PARTIAL
    else:
        final_status = LeadBrainDiscoveryJob.STATUS_COMPLETE

    run.status = final_status
    run.completed_at = timezone.now()
    run.finished_at = run.completed_at
    run.status_note = (
        f"Discovery run finished. Saved {run.total_candidates_saved} candidate(s), "
        f"skipped {run.total_duplicates_skipped} duplicate(s), "
        f"skipped {run.total_weak_skipped} weak candidate(s), and "
        f"failed {run.total_failed} candidate(s)."
    )
    run.save(update_fields=["status", "completed_at", "finished_at", "status_note", "updated_at"])
    _sync_upload_from_run(run)
    _sync_job_from_run(run, finalized=True)
    return run


def process_discovery_runs(*, limit=1, batch_size=DISCOVERY_DEFAULT_BATCH_SIZE, run_id=None) -> int:
    processed = 0
    for index in range(max(1, int(limit or 1))):
        target_run_id = run_id if index == 0 else None
        run = _claim_run(target_run_id)
        if not run:
            break
        if not run.queries_json or not run.candidates.exists():
            initialize_discovery_run(run)
            run.refresh_from_db()
        processed += process_discovery_run_batch(run, batch_size=max(1, int(batch_size or DISCOVERY_DEFAULT_BATCH_SIZE)))
        if run_id:
            break
    return processed


def run_discovery_job(job: LeadBrainDiscoveryJob, *, batch_size=DISCOVERY_DEFAULT_BATCH_SIZE) -> DiscoveryRunResult:
    run = queue_manual_discovery_run(job, created_by=job.created_by)
    while True:
        process_discovery_runs(limit=1, batch_size=batch_size, run_id=run.pk)
        run.refresh_from_db()
        if run.status not in LeadBrainDiscoveryRun.ACTIVE_STATUSES:
            break
    return DiscoveryRunResult(
        results_found=run.total_candidates_found,
        candidates_saved=run.total_candidates_saved,
        duplicates_skipped=run.total_duplicates_skipped,
        weak_skipped=run.total_weak_skipped,
        failed_candidates=run.total_failed,
    )
