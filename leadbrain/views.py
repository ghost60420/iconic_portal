import logging
import json
import os
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db import IntegrityError
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from .forms import (
    LeadBrainCompanyNotesForm,
    LeadBrainDiscoveryJobForm,
    LeadBrainUploadForm,
)
from .models import LeadBrainCompany, LeadBrainDiscoveryJob, LeadBrainDiscoveryRun, LeadBrainUpload
from .services.background_runner import launch_upload_processing, queue_parse_upload
from .services.discovery_service import can_queue_discovery_job
from .services.lead_export import create_lead_from_company
from .services.repair_service import repair_uploads
from .tasks import run_discovery_job_task
from .services.upload_state import (
    ACTIVE_UPLOAD_STATUSES,
    compute_uploaded_file_hash,
    find_active_duplicate_upload,
    is_upload_stale,
    release_stale_upload,
)


logger = logging.getLogger(__name__)
UPLOAD_PREVIEW_SESSION_KEY = "leadbrain_upload_preview"


class LeadBrainStaffOnlyMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return bool(user and (user.is_staff or user.is_superuser))


def _redirect_to_results_next(request):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url:
        parsed = urlparse(next_url)
        if not parsed.scheme and not parsed.netloc and next_url.startswith("/lead-brain/"):
            return redirect(next_url)
    return redirect("leadbrain_results")


def _active_duplicate_upload(user, *, file_hash: str = ""):
    return find_active_duplicate_upload(
        user_id=user.pk if getattr(user, "is_authenticated", False) else None,
        file_hash=file_hash,
    )


def _upload_is_active(upload: LeadBrainUpload) -> bool:
    if is_upload_stale(upload):
        release_stale_upload(
            upload,
            reason="Marked failed after no Lead Brain progress was detected. You can retry or delete it now.",
        )
        upload.refresh_from_db()
        return False
    return upload.status in ACTIVE_UPLOAD_STATUSES


def _discovery_jobs_queryset():
    return LeadBrainDiscoveryJob.objects.select_related("created_by", "upload")


def _discovery_dashboard_context(*, form=None):
    jobs = _discovery_jobs_queryset()
    return {
        "form": form or LeadBrainDiscoveryJobForm(),
        "jobs": jobs[:50],
        "total_jobs": jobs.count(),
        "active_jobs": jobs.filter(
            status__in=[LeadBrainDiscoveryJob.STATUS_QUEUED, LeadBrainDiscoveryJob.STATUS_PROCESSING],
            is_paused=False,
        ).count(),
        "paused_jobs": jobs.filter(is_paused=True).count(),
        "latest_runs": LeadBrainDiscoveryRun.objects.select_related("job", "upload")[:12],
    }


class LeadBrainHomeView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fit_counts = LeadBrainCompany.objects.filter(is_active=True).values("fit_label").annotate(total=Count("id"))
        fit_map = {row["fit_label"]: row["total"] for row in fit_counts}
        context.update(
            {
                "total_uploads": LeadBrainUpload.objects.filter(is_active=True).count(),
                "total_companies": LeadBrainCompany.objects.filter(is_active=True).count(),
                "good_fit_count": fit_map.get(LeadBrainCompany.FIT_GOOD, 0),
                "possible_fit_count": fit_map.get(LeadBrainCompany.FIT_POSSIBLE, 0),
                "weak_fit_count": fit_map.get(LeadBrainCompany.FIT_WEAK, 0),
                "recent_uploads": LeadBrainUpload.objects.filter(is_active=True).select_related("uploaded_by")[:10],
                "recent_discovery_jobs": LeadBrainDiscoveryJob.objects.select_related("created_by", "upload")[:8],
            }
        )
        return context


class LeadBrainTopMatchesView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/top_matches.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = (
            LeadBrainCompany.objects.select_related("upload", "moved_to_lead")
            .filter(
                is_active=True,
                moved_to_leads=False,
                fit_score__gte=80,
            )
            .filter(
                website__gt="",
            )
            .filter(
                Q(email__gt="")
                | Q(phone__gt="")
                | Q(linkedin_url__gt="")
            )
            .exclude(suggested_action__iexact="Not Relevant")
            .order_by("-fit_score", "-created_at", "company_name", "id")
        )

        paginator = Paginator(queryset, 50)
        page_obj = paginator.get_page(self.request.GET.get("page"))

        context.update(
            {
                "page_obj": page_obj,
                "companies": page_obj.object_list,
                "total_count": queryset.count(),
                "strong_count": queryset.count(),
                "possible_count": 0,
                "discovery_count": queryset.exclude(discovery_job__isnull=True).count(),
                "upload_count": queryset.filter(discovery_job__isnull=True).count(),
                "email_count": queryset.exclude(email="").count(),
                "phone_count": queryset.exclude(phone="").count(),
                "linkedin_count": queryset.exclude(linkedin_url="").count(),
            }
        )
        return context


class LeadBrainDiscoveryJobsView(LoginRequiredMixin, View):
    template_name = "leadbrain/discovery_jobs.html"

    def get(self, request):
        return render(request, self.template_name, _discovery_dashboard_context())


class LeadBrainDiscoveryJobCreateView(LoginRequiredMixin, View):
    template_name = "leadbrain/discovery_job_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": LeadBrainDiscoveryJobForm(), "job": None})

    def post(self, request):
        form = LeadBrainDiscoveryJobForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "job": None})

        job = form.save(commit=False)
        job.created_by = request.user
        job.status = (
            LeadBrainDiscoveryJob.STATUS_COMPLETE
            if job.schedule_type == LeadBrainDiscoveryJob.SCHEDULE_MANUAL
            else LeadBrainDiscoveryJob.STATUS_QUEUED
        )
        job.status_note = "Discovery job created."
        if job.schedule_type != LeadBrainDiscoveryJob.SCHEDULE_MANUAL:
            job.next_run_at = job.compute_next_run_at()
            job.status_note = "Discovery job scheduled."
        job.save()
        messages.success(request, "Discovery job created.")
        return redirect("leadbrain_discovery_job_detail", pk=job.pk)


class LeadBrainDiscoveryJobEditView(LoginRequiredMixin, View):
    template_name = "leadbrain/discovery_job_form.html"

    def get(self, request, pk):
        job = get_object_or_404(_discovery_jobs_queryset(), pk=pk)
        return render(request, self.template_name, {"form": LeadBrainDiscoveryJobForm(instance=job), "job": job})

    def post(self, request, pk):
        job = get_object_or_404(_discovery_jobs_queryset(), pk=pk)
        form = LeadBrainDiscoveryJobForm(request.POST, instance=job)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "job": job})
        job = form.save(commit=False)
        if job.schedule_type == LeadBrainDiscoveryJob.SCHEDULE_MANUAL:
            job.next_run_at = None
        elif not job.is_paused:
            job.next_run_at = job.compute_next_run_at(reference=timezone.now())
        job.status_note = "Discovery job updated."
        job.save()
        messages.success(request, "Discovery job updated.")
        return redirect("leadbrain_discovery_job_detail", pk=job.pk)


class LeadBrainDiscoveryJobDetailView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/discovery_job_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = get_object_or_404(_discovery_jobs_queryset(), pk=kwargs["pk"])
        runs = job.runs.select_related("upload")[:20]
        context.update(
            {
                "job": job,
                "recent_runs": runs,
                "saved_leads": LeadBrainCompany.objects.filter(
                    upload__discovery_runs__job=job,
                    is_active=True,
                ).distinct().count(),
                "strong_fits": LeadBrainCompany.objects.filter(
                    upload__discovery_runs__job=job,
                    fit_score__gte=80,
                    is_active=True,
                ).distinct().count(),
            }
        )
        return context


class LeadBrainDiscoveryRunDetailView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/discovery_run_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = get_object_or_404(
            LeadBrainDiscoveryRun.objects.select_related("job", "upload"),
            pk=kwargs["pk"],
        )
        candidates = run.candidates.select_related("created_leadbrain_company")[:100]
        context.update(
            {
                "run": run,
                "job": run.job,
                "candidates": candidates,
            }
        )
        return context


class LeadBrainDiscoveryJobRunNowView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(_discovery_jobs_queryset(), pk=pk)
        if job.runs.filter(status__in=LeadBrainDiscoveryRun.ACTIVE_STATUSES).exists():
            messages.info(request, "This discovery job already has an active queued or processing run.")
            return redirect("leadbrain_discovery_job_detail", pk=job.pk)
        allowed, reason = can_queue_discovery_job(user=request.user)
        if not allowed:
            messages.error(request, reason)
            return redirect("leadbrain_discovery_jobs")
        job.status = LeadBrainDiscoveryJob.STATUS_QUEUED
        job.status_note = "Discovery job queued for an immediate run."
        if job.schedule_type != LeadBrainDiscoveryJob.SCHEDULE_MANUAL and not job.is_paused:
            job.next_run_at = job.compute_next_run_at(reference=timezone.now())
        job.save(update_fields=["status", "status_note", "next_run_at", "updated_at"])
        transaction.on_commit(lambda: run_discovery_job_task.delay(job.pk))
        messages.success(request, "Discovery job queued.")
        return redirect("leadbrain_discovery_job_detail", pk=job.pk)


class LeadBrainDiscoveryJobPauseView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(_discovery_jobs_queryset(), pk=pk)
        job.is_paused = not job.is_paused
        if job.is_paused:
            job.next_run_at = None
            job.status_note = "Discovery job paused."
        elif job.schedule_type != LeadBrainDiscoveryJob.SCHEDULE_MANUAL:
            job.next_run_at = job.compute_next_run_at(reference=timezone.now())
            job.status_note = "Discovery job resumed."
        else:
            job.status_note = "Discovery job unpaused."
        job.save(update_fields=["is_paused", "next_run_at", "status_note", "updated_at"])
        messages.success(request, "Discovery job status updated.")
        return redirect("leadbrain_discovery_jobs")


class LeadBrainDiscoveryJobDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(_discovery_jobs_queryset(), pk=pk)
        if job.status == LeadBrainDiscoveryJob.STATUS_PROCESSING:
            messages.error(request, "Processing discovery jobs cannot be deleted while they are running.")
            return redirect("leadbrain_discovery_jobs")
        job.delete()
        messages.success(request, "Discovery job deleted.")
        return redirect("leadbrain_discovery_jobs")


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
        file_hash = compute_uploaded_file_hash(upload_file)
        existing_upload = _active_duplicate_upload(
            request.user,
            file_hash=file_hash,
        )
        if existing_upload:
            messages.info(
                request,
                f"{existing_upload.file_name or 'This file'} is already queued or processing under upload job #{existing_upload.pk}.",
            )
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")

        try:
            upload = form.save(commit=False)
            upload.uploaded_by = request.user
            upload.file_name = file_name
            upload.file_size = file_size
            upload.file_hash = file_hash
            upload.status = LeadBrainUpload.STATUS_QUEUED
            upload.status_note = "Upload queued for background parsing."
            upload.save()
            transaction.on_commit(lambda: queue_parse_upload(upload.pk))
        except IntegrityError:
            existing_upload = _active_duplicate_upload(request.user, file_hash=file_hash)
            if existing_upload:
                messages.info(
                    request,
                    f"{existing_upload.file_name or 'This file'} is already queued or processing under upload job #{existing_upload.pk}.",
                )
                return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")
            messages.error(request, "A duplicate Lead Brain upload is already active.")
            return redirect("leadbrain_upload")
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


class LeadBrainUploadRetryView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        if _upload_is_active(upload):
            messages.error(request, "Active uploads must be cancelled before a full retry.")
            return _redirect_to_results_next(request)
        existing_upload = _active_duplicate_upload(
            request.user,
            file_hash=upload.file_hash,
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
        upload.blank_rows = 0
        upload.pending_rows = 0
        upload.processing_rows = 0
        upload.completed_rows = 0
        upload.failed_rows = 0
        upload.progress_percent = 0
        upload.detected_columns_json = []
        upload.sample_rows_json = []
        upload.invalid_row_examples_json = []
        upload.duplicate_row_examples_json = []
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
                "blank_rows",
                "pending_rows",
                "processing_rows",
                "completed_rows",
                "failed_rows",
                "progress_percent",
                "detected_columns_json",
                "sample_rows_json",
                "invalid_row_examples_json",
                "duplicate_row_examples_json",
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
        include_inactive = self.request.GET.get("include_inactive") == "1"
        uploads = LeadBrainUpload.objects.select_related("uploaded_by")
        if not include_inactive:
            uploads = uploads.filter(is_active=True)
        context["uploads"] = uploads
        context["include_inactive"] = include_inactive
        return context


class LeadBrainResultsView(LoginRequiredMixin, TemplateView):
    template_name = "leadbrain/results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = LeadBrainCompany.objects.select_related("upload", "moved_to_lead")

        q = (self.request.GET.get("q") or "").strip()
        fit_label = (self.request.GET.get("fit_label") or "").strip()
        research_status = (self.request.GET.get("research_status") or "").strip()
        country = (self.request.GET.get("country") or "").strip()
        upload_id = (self.request.GET.get("upload") or "").strip()
        sort = (self.request.GET.get("sort") or "-fit_score").strip()
        reviewed = (self.request.GET.get("reviewed") or "").strip()
        include_moved = self.request.GET.get("include_moved") == "1"
        include_inactive = self.request.GET.get("include_inactive") == "1"

        if not include_moved:
            queryset = queryset.filter(moved_to_leads=False)
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

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
                "include_moved": include_moved,
                "include_inactive": include_inactive,
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
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload", "moved_to_lead"), pk=pk)
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
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload", "moved_to_lead"), pk=pk)
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


class LeadBrainCompanyMoveToLeadsView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload", "moved_to_lead"), pk=pk)
        result = create_lead_from_company(company)
        if result.created:
            messages.success(request, result.message)
        else:
            messages.warning(request, result.message)
        return _redirect_to_results_next(request)
