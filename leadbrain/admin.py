from django.contrib import admin

from .models import LeadBrainCompany, LeadBrainUpload


@admin.register(LeadBrainUpload)
class LeadBrainUploadAdmin(admin.ModelAdmin):
    list_display = ["id", "file_name", "uploaded_by", "status", "row_count", "uploaded_at"]
    list_filter = ["status", "uploaded_at"]
    search_fields = ["file_name", "uploaded_by__username"]
    ordering = ["-uploaded_at"]


@admin.register(LeadBrainCompany)
class LeadBrainCompanyAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "company_name",
        "fit_label",
        "fit_score",
        "website",
        "email",
        "country",
        "reviewed",
    ]
    list_filter = ["fit_label", "reviewed", "country", "created_at"]
    search_fields = ["company_name", "email", "website", "best_contact_name", "best_contact_title"]
    ordering = ["-fit_score", "company_name"]

