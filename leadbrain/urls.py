from django.urls import path

from .views import (
    LeadBrainCompanyDetailView,
    LeadBrainCompanyDeleteView,
    LeadBrainCompanyMarkNotRelevantView,
    LeadBrainHomeView,
    LeadBrainOpsView,
    LeadBrainOpsRepairView,
    LeadBrainResultsView,
    LeadBrainUploadCancelView,
    LeadBrainUploadRetryView,
    LeadBrainStartAnalysisView,
    LeadBrainUploadDeleteView,
    LeadBrainUploadListView,
    LeadBrainUploadView,
)


urlpatterns = [
    path("", LeadBrainHomeView.as_view(), name="leadbrain_home"),
    path("upload/", LeadBrainUploadView.as_view(), name="leadbrain_upload"),
    path("uploads/", LeadBrainUploadListView.as_view(), name="leadbrain_uploads"),
    path("uploads/<int:pk>/start-analysis/", LeadBrainStartAnalysisView.as_view(), name="leadbrain_start_analysis"),
    path("uploads/<int:pk>/retry/", LeadBrainUploadRetryView.as_view(), name="leadbrain_upload_retry"),
    path("uploads/<int:pk>/cancel/", LeadBrainUploadCancelView.as_view(), name="leadbrain_upload_cancel"),
    path("uploads/<int:pk>/delete/", LeadBrainUploadDeleteView.as_view(), name="leadbrain_upload_delete"),
    path("ops/", LeadBrainOpsView.as_view(), name="leadbrain_ops"),
    path("ops/repair/", LeadBrainOpsRepairView.as_view(), name="leadbrain_ops_repair"),
    path("results/", LeadBrainResultsView.as_view(), name="leadbrain_results"),
    path("company/<int:pk>/", LeadBrainCompanyDetailView.as_view(), name="leadbrain_company_detail"),
    path("company/<int:pk>/delete/", LeadBrainCompanyDeleteView.as_view(), name="leadbrain_company_delete"),
    path(
        "company/<int:pk>/mark-not-relevant/",
        LeadBrainCompanyMarkNotRelevantView.as_view(),
        name="leadbrain_company_mark_not_relevant",
    ),
]
