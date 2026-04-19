import logging
import json
import os
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from .forms import LeadBrainCompanyNotesForm, LeadBrainUploadForm
from .models import LeadBrainCompany, LeadBrainUpload
from .services.background_runner import launch_upload_processing, queue_parse_upload
from .services.repair_service import repair_uploads


logger = logging.getLogger(__name__)
UPLOAD_PREVIEW_SESSION_KEY = "leadbrain_upload_preview"
ACTIVE_UPLOAD_STATUSES = [
    LeadBrainUpload.STATUS_QUEUED,
    LeadBrainUpload.STATUS_PARSING,
    LeadBrainUpload.STATUS_PROCESSING,
]
def _redirect_to_results_next(request):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url:
        parsed = urlparse(next_url)
        if not parsed.scheme and not parsed.netloc and next_url.startswith("/lead-brain/"):
            return redirect(next_url)
    return redirect("leadbrain_results")


def _active_duplicate_upload(user, *, file_hash: str = "", file_name: str = "", file_size: int = 0):
    if not getattr(user, "is_authenticated", False):
        return None
    queryset = LeadBrainUpload.objects.filter(uploaded_by=user, status__in=ACTIVE_UPLOAD_STATUSES)
    if file_hash:
        duplicate = queryset.filter(file_hash=file_hash).order_by("-uploaded_at", "-id").first()
        if duplicate:
            return duplicate
    if file_name and file_size:
        return queryset.filter(file_name=file_name, file_size=file_size).order_by("-uploaded_at", "-id").first()
    return None


def _upload_is_active(upload: LeadBrainUpload) -> bool:
    return upload.status in ACTIVE_UPLOAD_STATUSES


class LeadBrainHomeView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fit_counts = LeadBrainCompany.objects.values("fit_label").annotate(total=Count("id"))
        fit_map = {row["fit_label"]: row["total"] for row in fit_counts}
        context.update(
            {
                "total_uploads": LeadBrainUpload.objects.count(),
                "total_companies": LeadBrainCompany.objects.count(),
                "good_fit_count": fit_map.get(LeadBrainCompany.FIT_GOOD, 0),
                "possible_fit_count": fit_map.get(LeadBrainCompany.FIT_POSSIBLE, 0),
                "weak_fit_count": fit_map.get(LeadBrainCompany.FIT_WEAK, 0),
                "recent_uploads": LeadBrainUpload.objects.select_related("uploaded_by")[:10],
            }
        )
        return context


class LeadBrainUploadView(LoginRequiredMixin, View):
    template_name = "leadbrain/upload.html"

    def get(self, request):
        return render(request, self.template_name, {"form": LeadBrainUploadForm()})

    def post(self, request):
        form = LeadBrainUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        upload_file = form.cleaned_data["file"]
        file_name = os.path.basename(upload_file.name or "")
        file_size = getattr(upload_file, "size", 0) or 0
        existing_upload = _active_duplicate_upload(
            request.user,
            file_name=file_name,
            file_size=file_size,
        )
        if existing_upload:
            messages.info(
                request,
                f"{existing_upload.file_name or 'This file'} is already queued or processing. "
                "The existing upload job is still running.",
            )
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")

        try:
            upload = form.save(commit=False)
            upload.uploaded_by = request.user
            upload.file_name = file_name
            upload.file_size = file_size
            upload.file_hash = ""
            upload.status = LeadBrainUpload.STATUS_QUEUED
            upload.status_note = "Upload queued for background parsing."
            upload.save()
            transaction.on_commit(lambda: queue_parse_upload(upload.pk))
        except Exception:
            logger.exception("leadbrain upload queue failed for %s", file_name or upload_file.name)
            if "upload" in locals() and upload.pk:
                upload.status = LeadBrainUpload.STATUS_FAILED
                upload.status_note = "The upload could not be queued for background parsing."
                upload.save(update_fields=["status", "status_note", "updated_at"])
            messages.error(request, "The upload could not be queued.")
            return redirect("leadbrain_upload")

        messages.success(
            request,
            "Upload received. Lead Brain Lite queued the file for background parsing and research.",
        )
        return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")


class LeadBrainStartAnalysisView(LoginRequiredMixin, View):
    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        upload.refresh_progress()

        try:
            if upload.status in [LeadBrainUpload.STATUS_QUEUED, LeadBrainUpload.STATUS_PARSING]:
                messages.info(request, "This upload is already queued for background parsing.")
                return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")

            pending_exists = upload.companies.filter(
                research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_FAILED]
            ).exists()
            if not pending_exists:
                messages.info(request, "There are no pending or failed Lead Brain rows left to process.")
                return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")

            upload.companies.filter(research_status=LeadBrainCompany.STATUS_FAILED).update(
                research_status=LeadBrainCompany.STATUS_PENDING,
                research_error="",
                updated_at=timezone.now(),
            )
            upload.status = LeadBrainUpload.STATUS_PROCESSING
            upload.status_note = "Background research and scoring are running."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            transaction.on_commit(lambda: launch_upload_processing(upload.pk))
            messages.success(request, "Lead Brain research resumed in the background.")
        except Exception:
            logger.exception("leadbrain resume queue failed for upload %s", upload.pk)
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.status_note = "Background research could not be queued."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            messages.error(request, "Lead Brain research could not be queued.")

        return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")


class LeadBrainStaffOnlyMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return bool(user and (user.is_staff or user.is_superuser))


class LeadBrainUploadRetryView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        if _upload_is_active(upload):
            messages.error(request, "Active uploads must be cancelled before a full retry.")
            return _redirect_to_results_next(request)
        existing_upload = _active_duplicate_upload(
            request.user,
            file_hash=upload.file_hash,
            file_name=upload.file_name,
            file_size=upload.file_size,
        )
        if existing_upload and existing_upload.pk != upload.pk:
            messages.info(
                request,
                f"{existing_upload.file_name or 'This file'} is already queued or processing under upload #{existing_upload.pk}.",
            )
            return _redirect_to_results_next(request)

        upload.companies.all().delete()
        upload.row_count = 0
        upload.source_row_count = 0
        upload.total_rows = 0
        upload.imported_rows = 0
        upload.skipped_duplicate_rows = 0
        upload.invalid_rows = 0
        upload.pending_rows = 0
        upload.processing_rows = 0
        upload.completed_rows = 0
        upload.failed_rows = 0
        upload.progress_percent = 0
        upload.detected_columns_json = []
        upload.sample_rows_json = []
        upload.invalid_row_examples_json = []
        upload.status = LeadBrainUpload.STATUS_QUEUED
        upload.status_note = "Upload retry requested and queued for background parsing."
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
        transaction.on_commit(lambda: queue_parse_upload(upload.pk))
        messages.success(request, "Lead Brain upload retry was queued.")
        return _redirect_to_results_next(request)


class LeadBrainUploadCancelView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        if upload.status in [LeadBrainUpload.STATUS_COMPLETE, LeadBrainUpload.STATUS_FAILED, LeadBrainUpload.STATUS_PARTIAL, LeadBrainUpload.STATUS_CANCELLED]:
            messages.info(request, "This upload is not active.")
            return _redirect_to_results_next(request)

        upload.status = LeadBrainUpload.STATUS_CANCELLED
        upload.status_note = "This upload was cancelled."
        upload.save(update_fields=["status", "status_note", "updated_at"])
        upload.companies.filter(research_status=LeadBrainCompany.STATUS_PENDING).update(
            research_status=LeadBrainCompany.STATUS_FAILED,
            research_error="Cancelled by user.",
            processed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        upload.refresh_progress()
        messages.success(request, f"{upload.file_name or f'Upload #{upload.pk}'} was cancelled.")
        return _redirect_to_results_next(request)


class LeadBrainCompanyDeleteView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload"), pk=pk)
        upload = company.upload

        if company.research_status in [LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_PROCESSING] and _upload_is_active(upload):
            messages.error(request, "This row is currently part of an active research job and cannot be deleted right now.")
            return _redirect_to_results_next(request)

        company_label = company.company_name or f"Company #{company.pk}"
        company.delete()

        if upload.companies.exists():
            upload.refresh_progress()
        else:
            upload.row_count = 0
            upload.total_rows = 0
            upload.pending_rows = 0
            upload.processing_rows = 0
            upload.completed_rows = 0
            upload.failed_rows = 0
            upload.progress_percent = 100
            if upload.status == LeadBrainUpload.STATUS_CANCELLED:
                upload.status_note = "All company rows were removed after this upload was cancelled."
            else:
                upload.status = LeadBrainUpload.STATUS_COMPLETE
                upload.status_note = "All company rows have been removed from this upload."
            upload.save(
                update_fields=[
                    "row_count",
                    "total_rows",
                    "pending_rows",
                    "processing_rows",
                    "completed_rows",
                    "failed_rows",
                    "progress_percent",
                    "status",
                    "status_note",
                    "updated_at",
                ]
            )

        messages.success(request, f"{company_label} was deleted from Lead Brain Lite.")
        return _redirect_to_results_next(request)


class LeadBrainCompanyMarkNotRelevantView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        company = get_object_or_404(LeadBrainCompany, pk=pk)
        if company.research_status in [LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_PROCESSING] and _upload_is_active(company.upload):
            messages.error(request, "This row is currently part of an active research job and cannot be updated right now.")
            return _redirect_to_results_next(request)
        company.fit_label = LeadBrainCompany.FIT_WEAK
        company.fit_score = 0
        company.suggested_action = "Not Relevant"
        company.reviewed = True
        company.save(update_fields=["fit_label", "fit_score", "suggested_action", "reviewed", "updated_at"])
        messages.success(request, f"{company.company_name or f'Company #{company.pk}'} was marked as not relevant.")
        return _redirect_to_results_next(request)


class LeadBrainUploadDeleteView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    template_name = "leadbrain/upload_confirm_delete.html"

    def get(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        return render(request, self.template_name, {"upload": upload})

    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        if _upload_is_active(upload):
            messages.error(request, "Active uploads cannot be deleted. Cancel the upload first.")
            return redirect("leadbrain_uploads")

        file_name = upload.file_name or f"Upload {upload.pk}"
        if upload.file:
            upload.file.delete(save=False)
        upload.delete()
        messages.success(request, f"{file_name} was deleted from Lead Brain Lite.")
        return redirect("leadbrain_uploads")


class LeadBrainUploadListView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/upload_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["uploads"] = LeadBrainUpload.objects.select_related("uploaded_by")
        return context


class LeadBrainResultsView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = LeadBrainCompany.objects.select_related("upload")

        q = (self.request.GET.get("q") or "").strip()
        fit_label = (self.request.GET.get("fit_label") or "").strip()
        research_status = (self.request.GET.get("research_status") or "").strip()
        country = (self.request.GET.get("country") or "").strip()
        upload_id = (self.request.GET.get("upload") or "").strip()
        sort = (self.request.GET.get("sort") or "-fit_score").strip()
        reviewed = (self.request.GET.get("reviewed") or "").strip()

        if q:
            queryset = queryset.filter(
                Q(company_name__icontains=q)
                | Q(email__icontains=q)
                | Q(website__icontains=q)
                | Q(best_contact_name__icontains=q)
            )

        if fit_label:
            queryset = queryset.filter(fit_label=fit_label)
        if research_status:
            queryset = queryset.filter(research_status=research_status)
        if country:
            queryset = queryset.filter(country__iexact=country)
        if upload_id.isdigit():
            queryset = queryset.filter(upload_id=int(upload_id))
        if reviewed in {"true", "false"}:
            queryset = queryset.filter(reviewed=(reviewed == "true"))

        has_website = self.request.GET.get("has_website") == "1"
        has_email = self.request.GET.get("has_email") == "1"
        has_phone = self.request.GET.get("has_phone") == "1"
        has_linkedin = self.request.GET.get("has_linkedin") == "1"

        if has_website:
            queryset = queryset.exclude(website="")
        if has_email:
            queryset = queryset.exclude(email="")
        if has_phone:
            queryset = queryset.exclude(phone="")
        if has_linkedin:
            queryset = queryset.exclude(linkedin_url="")

        if sort not in {"-fit_score", "fit_score", "company_name", "-created_at"}:
            sort = "-fit_score"
        queryset = queryset.order_by(sort, "company_name", "id")

        paginator = Paginator(queryset, 50)
        page_obj = paginator.get_page(self.request.GET.get("page"))

        processing_count = queryset.filter(research_status=LeadBrainCompany.STATUS_PROCESSING).count()
        pending_count = queryset.filter(research_status=LeadBrainCompany.STATUS_PENDING).count()
        complete_count = queryset.filter(research_status=LeadBrainCompany.STATUS_COMPLETE).count()
        failed_count = queryset.filter(research_status=LeadBrainCompany.STATUS_FAILED).count()
        selected_upload = None
        if upload_id.isdigit():
            selected_upload = LeadBrainUpload.objects.filter(pk=int(upload_id)).first()

        context.update(
            {
                "page_obj": page_obj,
                "companies": page_obj.object_list,
                "fit_label": fit_label,
                "research_status": research_status,
                "country": country,
                "query": q,
                "sort": sort,
                "upload_id": upload_id,
                "reviewed": reviewed,
                "has_website": has_website,
                "has_email": has_email,
                "has_phone": has_phone,
                "has_linkedin": has_linkedin,
                "country_options": LeadBrainCompany.objects.exclude(country="").order_by("country").values_list("country", flat=True).distinct(),
                "upload_options": LeadBrainUpload.objects.only("id", "file_name").order_by("-uploaded_at")[:30],
                "total_count": queryset.count(),
                "good_fit_count": queryset.filter(fit_label=LeadBrainCompany.FIT_GOOD).count(),
                "possible_fit_count": queryset.filter(fit_label=LeadBrainCompany.FIT_POSSIBLE).count(),
                "weak_fit_count": queryset.filter(fit_label=LeadBrainCompany.FIT_WEAK).count(),
                "pending_count": pending_count,
                "processing_count": processing_count,
                "complete_count": complete_count,
                "failed_count": failed_count,
                "selected_upload": selected_upload,
                "auto_refresh": bool(
                    selected_upload
                    and selected_upload.status
                    in [LeadBrainUpload.STATUS_QUEUED, LeadBrainUpload.STATUS_PARSING, LeadBrainUpload.STATUS_PROCESSING]
                ),
            }
        )
        return context


class LeadBrainOpsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "leadbrain/ops.html"

    def test_func(self):
        user = self.request.user
        return bool(user and (user.is_staff or user.is_superuser))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        duplicate_note = "Duplicate upload history for review"
        context.update(
            {
                "failed_uploads": LeadBrainUpload.objects.select_related("uploaded_by").filter(
                    status__in=[LeadBrainUpload.STATUS_FAILED, LeadBrainUpload.STATUS_PARTIAL, LeadBrainUpload.STATUS_CANCELLED]
                )[:50],
                "flagged_duplicates": LeadBrainUpload.objects.select_related("uploaded_by").filter(
                    status_note__icontains=duplicate_note
                )[:50],
                "active_uploads": LeadBrainUpload.objects.select_related("uploaded_by").filter(
                    status__in=[LeadBrainUpload.STATUS_QUEUED, LeadBrainUpload.STATUS_PARSING, LeadBrainUpload.STATUS_PROCESSING]
                )[:50],
            }
        )
        return context


class LeadBrainOpsRepairView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request):
        result = repair_uploads(
            apply_changes=True,
            stale_minutes=60,
            flag_duplicates=True,
            backfill_hashes=True,
        )
        messages.success(
            request,
            "Lead Brain repair complete. "
            f"Stale uploads reviewed: {result['stale_uploads']}. "
            f"Hashes backfilled: {result['backfilled_hashes']}. "
            f"Duplicate groups reviewed: {result['duplicate_groups']}.",
        )
        return redirect("leadbrain_ops")


class LeadBrainCompanyDetailView(LoginRequiredMixin, View):
    template_name = "leadbrain/company_detail.html"

    def get(self, request, pk):
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload"), pk=pk)
        return render(
            request,
            self.template_name,
            {
                "company": company,
                "notes_form": LeadBrainCompanyNotesForm(instance=company),
                "raw_row_pretty": json.dumps(company.raw_row_json or {}, indent=2, sort_keys=True),
                "research_pretty": json.dumps(company.research_json or {}, indent=2, sort_keys=True),
            },
        )

    def post(self, request, pk):
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload"), pk=pk)
        form = LeadBrainCompanyNotesForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Notes updated.")
            return redirect("leadbrain_company_detail", pk=company.pk)

        return render(
            request,
            self.template_name,
            {
                "company": company,
                "notes_form": form,
                "raw_row_pretty": json.dumps(company.raw_row_json or {}, indent=2, sort_keys=True),
                "research_pretty": json.dumps(company.research_json or {}, indent=2, sort_keys=True),
            },
        )
