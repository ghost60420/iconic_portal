from django.urls import path

from .views import (
    LeadBrainCompanyDetailView,
    LeadBrainHomeView,
    LeadBrainOpsView,
    LeadBrainResultsView,
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
    path("uploads/<int:pk>/delete/", LeadBrainUploadDeleteView.as_view(), name="leadbrain_upload_delete"),
    path("ops/", LeadBrainOpsView.as_view(), name="leadbrain_ops"),
    path("results/", LeadBrainResultsView.as_view(), name="leadbrain_results"),
    path("company/<int:pk>/", LeadBrainCompanyDetailView.as_view(), name="leadbrain_company_detail"),
]
