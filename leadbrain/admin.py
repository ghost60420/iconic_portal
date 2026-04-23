from django.contrib import admin

from .models import (
    LeadBrainCompany,
    LeadBrainDiscoveryCandidate,
    LeadBrainDiscoveryJob,
    LeadBrainDiscoveryRun,
    LeadBrainUpload,
    LeadBrainWorker,
)


@admin.register(LeadBrainUpload)
class LeadBrainUploadAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "file_name",
        "uploaded_by",
        "is_active",
        "status",
        "source_row_count",
        "imported_rows",
        "skipped_duplicate_rows",
        "invalid_rows",
        "total_rows",
        "completed_rows",
        "failed_rows",
        "progress_percent",
        "updated_at",
        "uploaded_at",
    ]
    list_filter = ["is_active", "status", "uploaded_at"]
    search_fields = ["file_name", "file_hash", "status_note", "inactive_reason", "uploaded_by__username"]
    ordering = ["-uploaded_at"]


@admin.register(LeadBrainCompany)
class LeadBrainCompanyAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "company_name",
        "is_active",
        "source_type",
        "discovery_run",
        "research_status",
        "duplicate_of",
        "moved_to_leads",
        "moved_to_lead",
        "moved_to_lead_code",
        "fit_label",
        "fit_score",
        "website",
        "email",
        "country",
        "reviewed",
    ]
    list_filter = ["is_active", "source_type", "research_status", "fit_label", "reviewed", "country", "created_at"]
    search_fields = ["company_name", "email", "website", "best_contact_name", "best_contact_title", "inactive_reason"]
    ordering = ["-fit_score", "company_name"]


@admin.register(LeadBrainWorker)
class LeadBrainWorkerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "status",
        "hostname",
        "pid",
        "current_upload",
        "heartbeat_at",
        "processed_batches",
        "processed_rows",
    ]
    list_filter = ["status", "hostname", "heartbeat_at"]
    search_fields = ["name", "hostname", "last_error"]
    ordering = ["name"]


@admin.register(LeadBrainDiscoveryJob)
class LeadBrainDiscoveryJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "source_type",
        "is_active",
        "schedule_type",
        "is_paused",
        "country",
        "niche",
        "status",
        "max_results",
        "total_runs",
        "total_strong_fits",
        "results_found",
        "candidates_saved",
        "duplicates_skipped",
        "weak_skipped",
        "created_by",
        "last_run_at",
    ]
    list_filter = ["status", "is_active", "schedule_type", "is_paused", "source_type", "country", "niche", "created_at"]
    search_fields = ["name", "status_note", "created_by__username"]
    ordering = ["-created_at"]


@admin.register(LeadBrainDiscoveryRun)
class LeadBrainDiscoveryRunAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "job",
        "status",
        "results_found",
        "candidates_saved",
        "total_duplicates_skipped",
        "total_failed",
        "strong_fits_found",
        "started_at",
        "completed_at",
    ]
    list_filter = ["status", "started_at", "completed_at"]
    search_fields = ["job__name", "job__status_note", "status_note", "error_message"]
    ordering = ["-created_at"]


@admin.register(LeadBrainDiscoveryCandidate)
class LeadBrainDiscoveryCandidateAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "run",
        "company_name",
        "discovery_status",
        "fit_score",
        "fit_label",
        "source_type",
        "country",
        "niche",
        "created_leadbrain_company",
        "updated_at",
    ]
    list_filter = ["discovery_status", "source_type", "country", "niche", "created_at"]
    search_fields = ["company_name", "website", "source_url", "skip_reason"]
    ordering = ["run_id", "id"]
