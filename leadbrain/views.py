import logging
import json
import os
from hashlib import sha256

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from .forms import LeadBrainCompanyNotesForm, LeadBrainUploadForm
from .models import LeadBrainCompany, LeadBrainUpload
from .services.background_runner import launch_upload_processing
from .services.file_parser import parse_uploaded_file


logger = logging.getLogger(__name__)
BULK_CREATE_BATCH_SIZE = 500


def _hash_uploaded_file(uploaded_file) -> str:
    digest = sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return digest.hexdigest()


def _active_duplicate_upload(user, file_hash: str):
    if not file_hash or not getattr(user, "is_authenticated", False):
        return None
    return (
        LeadBrainUpload.objects.filter(
            uploaded_by=user,
            file_hash=file_hash,
            status__in=[LeadBrainUpload.STATUS_PENDING, LeadBrainUpload.STATUS_PROCESSING],
        )
        .order_by("-uploaded_at", "-id")
        .first()
    )


def _launch_upload_after_commit(upload_id: int) -> None:
    try:
        launch_upload_processing(upload_id)
        LeadBrainUpload.objects.filter(pk=upload_id, status=LeadBrainUpload.STATUS_PENDING).update(
            status=LeadBrainUpload.STATUS_PROCESSING,
            status_note="Background batch analysis is running.",
            updated_at=timezone.now(),
        )
    except Exception:
        logger.exception("leadbrain background launch failed for upload %s", upload_id)
        LeadBrainUpload.objects.filter(pk=upload_id).update(
            status=LeadBrainUpload.STATUS_FAILED,
            status_note="Background batch analysis could not be started.",
            updated_at=timezone.now(),
        )


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
        file_hash = _hash_uploaded_file(upload_file)
        existing_upload = _active_duplicate_upload(request.user, file_hash)
        if existing_upload:
            messages.info(
                request,
                f"{existing_upload.file_name or 'This file'} is already processing. "
                "The existing upload job is still running.",
            )
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")

        try:
            upload = form.save(commit=False)
            upload.uploaded_by = request.user
            upload.file_name = os.path.basename(upload_file.name or "")
            upload.file_hash = file_hash
            upload.status = LeadBrainUpload.STATUS_PENDING
            upload.status_note = ""
            upload.save()
        except IntegrityError:
            existing_upload = _active_duplicate_upload(request.user, file_hash)
            if existing_upload:
                messages.info(
                    request,
                    f"{existing_upload.file_name or 'This file'} is already processing. "
                    "The existing upload job is still running.",
                )
                return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")
            logger.exception("leadbrain upload duplicate protection failed for file hash %s", file_hash)
            messages.error(request, "This upload could not be started.")
            return redirect("leadbrain_upload")

        try:
            rows = parse_uploaded_file(upload.file.path)
        except Exception as exc:
            logger.exception("leadbrain upload parse failed for upload %s", upload.pk)
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.status_note = "The uploaded file could not be parsed."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            form.add_error("file", str(exc))
            messages.error(request, "The upload could not be processed.")
            return render(request, self.template_name, {"form": form})

        try:
            total_rows = len(rows)
            companies = []
            for row in rows:
                companies.append(
                    LeadBrainCompany(
                        upload=upload,
                        row_number=row.get("row_number", 0),
                        company_name=row.get("company_name", ""),
                        website=row.get("website", ""),
                        email=row.get("email", ""),
                        phone=row.get("phone", ""),
                        country=row.get("country", ""),
                        city=row.get("city", ""),
                        raw_row_json=row.get("raw_row_json", {}),
                        fit_label="",
                        fit_score=0,
                        suggested_action="Run Research",
                        research_status=LeadBrainCompany.STATUS_PENDING,
                    )
                )

            with transaction.atomic():
                if companies:
                    LeadBrainCompany.objects.bulk_create(companies, batch_size=BULK_CREATE_BATCH_SIZE)

                upload.row_count = total_rows
                upload.total_rows = total_rows
                upload.pending_rows = total_rows
                upload.processing_rows = 0
                upload.completed_rows = 0
                upload.failed_rows = 0
                upload.progress_percent = 0
                upload.status = LeadBrainUpload.STATUS_PENDING if total_rows else LeadBrainUpload.STATUS_FAILED
                upload.status_note = (
                    "Rows are saved and queued for background batch analysis."
                    if total_rows
                    else "No usable rows were found in the uploaded file."
                )
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

                if total_rows:
                    transaction.on_commit(lambda upload_id=upload.pk: _launch_upload_after_commit(upload_id))
        except Exception:
            logger.exception("leadbrain upload row save failed for upload %s", upload.pk)
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.status_note = "The upload failed before company rows could be saved."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            messages.error(request, "The upload failed before the full process could complete.")
            return redirect("leadbrain_upload")

        messages.success(
            request,
            f"Upload received. {upload.total_rows} company row(s) were saved and queued for background analysis.",
        )
        return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")


class LeadBrainStartAnalysisView(LoginRequiredMixin, View):
    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        upload.refresh_progress()

        if upload.status == LeadBrainUpload.STATUS_PROCESSING:
            messages.info(request, "Lead Brain analysis is already running for this upload.")
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")

        pending_exists = upload.companies.filter(
            research_status__in=[LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_FAILED]
        ).exists()
        if not pending_exists:
            messages.info(request, "There are no pending Lead Brain rows left to analyze.")
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")

        try:
            launch_upload_processing(upload.pk)
            upload.status = LeadBrainUpload.STATUS_PROCESSING
            upload.status_note = "Background batch analysis is running."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            messages.success(request, "Lead Brain analysis started in the background.")
        except Exception:
            logger.exception("leadbrain background launch failed for upload %s", upload.pk)
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.status_note = "Background batch analysis could not be started."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            messages.error(request, "Lead Brain analysis could not be started.")

        return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")


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
                "auto_refresh": bool(selected_upload and selected_upload.status == LeadBrainUpload.STATUS_PROCESSING),
            }
        )
        return context


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
