from django.contrib import admin

from .models import LeadBrainCompany, LeadBrainUpload, LeadBrainWorker


@admin.register(LeadBrainUpload)
class LeadBrainUploadAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "file_name",
        "uploaded_by",
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
    list_filter = ["status", "uploaded_at"]
    search_fields = ["file_name", "file_hash", "status_note", "uploaded_by__username"]
    ordering = ["-uploaded_at"]


@admin.register(LeadBrainCompany)
class LeadBrainCompanyAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "company_name",
        "research_status",
        "fit_label",
        "fit_score",
        "website",
        "email",
        "country",
        "reviewed",
    ]
    list_filter = ["research_status", "fit_label", "reviewed", "country", "created_at"]
    search_fields = ["company_name", "email", "website", "best_contact_name", "best_contact_title"]
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
