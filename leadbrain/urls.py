from django.urls import path

from .views import (
    LeadBrainCompanyDetailView,
    LeadBrainHomeView,
    LeadBrainResultsView,
    LeadBrainUploadListView,
    LeadBrainUploadView,
)


urlpatterns = [
    path("", LeadBrainHomeView.as_view(), name="leadbrain_home"),
    path("upload/", LeadBrainUploadView.as_view(), name="leadbrain_upload"),
    path("uploads/", LeadBrainUploadListView.as_view(), name="leadbrain_uploads"),
    path("results/", LeadBrainResultsView.as_view(), name="leadbrain_results"),
    path("company/<int:pk>/", LeadBrainCompanyDetailView.as_view(), name="leadbrain_company_detail"),
]

