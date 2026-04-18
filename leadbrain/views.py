import logging
import json
import os
import secrets
import tempfile
from urllib.parse import urlparse
from datetime import timedelta
from hashlib import sha256

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files import File
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from .forms import LeadBrainCompanyNotesForm, LeadBrainUploadForm
from .models import LeadBrainCompany, LeadBrainUpload, LeadBrainWorker
from .services.background_runner import launch_upload_processing
from .services.file_parser import parse_uploaded_file_report
from .services.import_service import prepare_import_rows


logger = logging.getLogger(__name__)
BULK_CREATE_BATCH_SIZE = 500
ACTIVE_WORKER_HEARTBEAT_SECONDS = 45
UPLOAD_PREVIEW_SESSION_KEY = "leadbrain_upload_preview"


def _hash_uploaded_file(uploaded_file) -> str:
    digest = sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return digest.hexdigest()


def _cleanup_upload_preview(request):
    preview = request.session.pop(UPLOAD_PREVIEW_SESSION_KEY, None)
    temp_path = (preview or {}).get("temp_path")
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            logger.warning("leadbrain preview temp cleanup failed for %s", temp_path)
    request.session.modified = True


def _store_preview_file(uploaded_file) -> str:
    suffix = os.path.splitext(uploaded_file.name or "")[1].lower()
    fd, temp_path = tempfile.mkstemp(prefix="leadbrain_preview_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in uploaded_file.chunks():
                handle.write(chunk)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return temp_path


def _preview_context(form, preview):
    return {
        "form": form,
        "preview": preview,
    }


def _redirect_to_results_next(request):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url:
        parsed = urlparse(next_url)
        if not parsed.scheme and not parsed.netloc and next_url.startswith("/lead-brain/"):
            return redirect(next_url)
    return redirect("leadbrain_results")


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


def _fresh_worker_for_upload(upload: LeadBrainUpload | None = None):
    cutoff = timezone.now() - timedelta(seconds=ACTIVE_WORKER_HEARTBEAT_SECONDS)
    queryset = LeadBrainWorker.objects.filter(
        status__in=[
            LeadBrainWorker.STATUS_STARTING,
            LeadBrainWorker.STATUS_IDLE,
            LeadBrainWorker.STATUS_RUNNING,
        ],
        heartbeat_at__gte=cutoff,
    )
    if upload is not None:
        queryset = queryset.filter(Q(current_upload=upload) | Q(current_upload__isnull=True))
    return queryset.order_by("name", "id").first()


def _launch_upload_after_commit(upload_id: int) -> None:
    try:
        existing_note = (
            LeadBrainUpload.objects.filter(pk=upload_id).values_list("status_note", flat=True).first() or ""
        )
        launch_upload_processing(upload_id)
        status_note = "Background batch analysis is running."
        if existing_note:
            status_note = f"{existing_note} {status_note}"
        LeadBrainUpload.objects.filter(pk=upload_id, status=LeadBrainUpload.STATUS_PENDING).update(
            status=LeadBrainUpload.STATUS_PROCESSING,
            status_note=status_note,
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
                "worker_statuses": LeadBrainWorker.objects.select_related("current_upload")[:5],
            }
        )
        return context


class LeadBrainUploadView(LoginRequiredMixin, View):
    template_name = "leadbrain/upload.html"

    def get(self, request):
        if request.GET.get("clear_preview") == "1":
            _cleanup_upload_preview(request)
        return render(request, self.template_name, {"form": LeadBrainUploadForm()})

    def post(self, request):
        preview_token = (request.POST.get("preview_token") or "").strip()
        if preview_token:
            return self._confirm_preview(request, preview_token)

        return self._preview_upload(request)

    def _preview_upload(self, request):
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
            temp_path = _store_preview_file(upload_file)
            parse_report = parse_uploaded_file_report(temp_path)
            import_report = prepare_import_rows(parse_report["rows"])
        except Exception as exc:
            logger.exception("leadbrain preview parse failed for %s", upload_file.name)
            if "temp_path" in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
            form.add_error("file", str(exc))
            messages.error(request, "The upload could not be processed.")
            return render(request, self.template_name, {"form": form})

        _cleanup_upload_preview(request)
        preview = {
            "token": secrets.token_urlsafe(24),
            "temp_path": temp_path,
            "file_name": os.path.basename(upload_file.name or ""),
            "file_hash": file_hash,
            "detected_columns": parse_report.get("detected_columns", []),
            "header_row_number": parse_report.get("header_row_number", 1),
            "source_row_count": parse_report.get("source_row_count", 0),
            "blank_rows": parse_report.get("blank_rows", 0),
            "sample_rows": parse_report.get("sample_rows", []),
            "imported_rows": import_report["imported_rows"],
            "skipped_duplicate_rows": import_report["skipped_duplicate_rows"],
            "invalid_rows": import_report["invalid_rows"],
            "invalid_reasons": import_report["invalid_reasons"],
        }
        request.session[UPLOAD_PREVIEW_SESSION_KEY] = preview
        request.session.modified = True

        return render(
            request,
            self.template_name,
            _preview_context(LeadBrainUploadForm(), preview),
        )

    def _confirm_preview(self, request, preview_token):
        preview = request.session.get(UPLOAD_PREVIEW_SESSION_KEY) or {}
        if preview.get("token") != preview_token:
            _cleanup_upload_preview(request)
            messages.error(request, "The upload preview expired. Please upload the file again.")
            return redirect("leadbrain_upload")

        temp_path = preview.get("temp_path") or ""
        if not temp_path or not os.path.exists(temp_path):
            _cleanup_upload_preview(request)
            messages.error(request, "The preview file is no longer available. Please upload the file again.")
            return redirect("leadbrain_upload")

        existing_upload = _active_duplicate_upload(request.user, preview.get("file_hash", ""))
        if existing_upload:
            _cleanup_upload_preview(request)
            messages.info(
                request,
                f"{existing_upload.file_name or 'This file'} is already processing. "
                "The existing upload job is still running.",
            )
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")

        try:
            with open(temp_path, "rb") as handle:
                upload = LeadBrainUpload(
                    uploaded_by=request.user,
                    file_name=preview.get("file_name", ""),
                    file_hash=preview.get("file_hash", ""),
                    status=LeadBrainUpload.STATUS_PENDING,
                    status_note="",
                )
                upload.file.save(preview.get("file_name", "upload.csv"), File(handle), save=False)
                upload.save()
        except IntegrityError:
            existing_upload = _active_duplicate_upload(request.user, preview.get("file_hash", ""))
            if existing_upload:
                _cleanup_upload_preview(request)
                messages.info(
                    request,
                    f"{existing_upload.file_name or 'This file'} is already processing. "
                    "The existing upload job is still running.",
                )
                return redirect(f"{reverse_lazy('leadbrain_results')}?upload={existing_upload.pk}")
            logger.exception(
                "leadbrain upload duplicate protection failed for file hash %s", preview.get("file_hash", "")
            )
            _cleanup_upload_preview(request)
            messages.error(request, "This upload could not be started.")
            return redirect("leadbrain_upload")
        except Exception:
            logger.exception("leadbrain upload create failed for preview %s", preview.get("file_name", ""))
            _cleanup_upload_preview(request)
            messages.error(request, "This upload could not be started.")
            return redirect("leadbrain_upload")

        try:
            parse_report = parse_uploaded_file_report(temp_path)
            import_report = prepare_import_rows(parse_report["rows"])
            import_rows = import_report["rows"]
            imported_rows = import_report["imported_rows"]
            skipped_duplicate_rows = import_report["skipped_duplicate_rows"]
            invalid_rows = import_report["invalid_rows"]
            invalid_reasons = import_report["invalid_reasons"]
            companies = []
            for row in import_rows:
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

            status_note = (
                "Rows are saved and queued for background batch analysis. "
                f"Imported {imported_rows} of {parse_report['source_row_count']} row(s), "
                f"skipped {skipped_duplicate_rows} duplicate row(s), "
                f"ignored {invalid_rows} invalid row(s)."
                if imported_rows
                else f"No rows were imported. Skipped {skipped_duplicate_rows} duplicate row(s) "
                f"and ignored {invalid_rows} invalid row(s)."
            )
            if invalid_reasons:
                status_note += " Invalid examples: " + "; ".join(invalid_reasons)

            with transaction.atomic():
                if companies:
                    LeadBrainCompany.objects.bulk_create(companies, batch_size=BULK_CREATE_BATCH_SIZE)

                upload.row_count = imported_rows
                upload.source_row_count = parse_report["source_row_count"]
                upload.total_rows = imported_rows
                upload.imported_rows = imported_rows
                upload.skipped_duplicate_rows = skipped_duplicate_rows
                upload.invalid_rows = invalid_rows
                upload.pending_rows = imported_rows
                upload.processing_rows = 0
                upload.completed_rows = 0
                upload.failed_rows = 0
                upload.progress_percent = 0
                upload.status = LeadBrainUpload.STATUS_PENDING if imported_rows else LeadBrainUpload.STATUS_FAILED
                upload.status_note = status_note
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
                        "status",
                        "status_note",
                        "updated_at",
                    ]
                )

                if imported_rows:
                    transaction.on_commit(lambda upload_id=upload.pk: _launch_upload_after_commit(upload_id))
        except Exception:
            logger.exception("leadbrain upload row save failed for upload %s", upload.pk)
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.status_note = "The upload failed before company rows could be saved."
            upload.save(update_fields=["status", "status_note", "updated_at"])
            messages.error(request, "The upload failed before the full process could complete.")
            return redirect("leadbrain_upload")
        finally:
            _cleanup_upload_preview(request)

        messages.success(
            request,
            f"Upload received. {upload.imported_rows} row(s) imported from {upload.source_row_count}, "
            f"{upload.skipped_duplicate_rows} duplicate row(s) skipped, "
            f"{upload.invalid_rows} invalid row(s) ignored.",
        )
        return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")


class LeadBrainStartAnalysisView(LoginRequiredMixin, View):
    def post(self, request, pk):
        upload = get_object_or_404(LeadBrainUpload, pk=pk)
        upload.refresh_progress()

        if upload.status == LeadBrainUpload.STATUS_PROCESSING:
            if _fresh_worker_for_upload(upload):
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


class LeadBrainStaffOnlyMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return bool(user and (user.is_staff or user.is_superuser))


class LeadBrainCompanyDeleteView(LoginRequiredMixin, LeadBrainStaffOnlyMixin, View):
    def post(self, request, pk):
        company = get_object_or_404(LeadBrainCompany.objects.select_related("upload"), pk=pk)
        upload = company.upload

        if company.research_status in [LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_PROCESSING] and _fresh_worker_for_upload(upload):
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
        if company.research_status in [LeadBrainCompany.STATUS_PENDING, LeadBrainCompany.STATUS_PROCESSING] and _fresh_worker_for_upload(company.upload):
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
        if upload.status in [LeadBrainUpload.STATUS_PENDING, LeadBrainUpload.STATUS_PROCESSING] and _fresh_worker_for_upload(upload):
            messages.error(request, "Active uploads cannot be deleted while background analysis is still running.")
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
        context["worker_statuses"] = LeadBrainWorker.objects.select_related("current_upload")
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
                    in [LeadBrainUpload.STATUS_PENDING, LeadBrainUpload.STATUS_PROCESSING]
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
                "workers": LeadBrainWorker.objects.select_related("current_upload"),
                "failed_uploads": LeadBrainUpload.objects.select_related("uploaded_by").filter(
                    status__in=[LeadBrainUpload.STATUS_FAILED, LeadBrainUpload.STATUS_PARTIAL]
                )[:50],
                "flagged_duplicates": LeadBrainUpload.objects.select_related("uploaded_by").filter(
                    status_note__icontains=duplicate_note
                )[:50],
                "active_uploads": LeadBrainUpload.objects.select_related("uploaded_by").filter(
                    status__in=[LeadBrainUpload.STATUS_PENDING, LeadBrainUpload.STATUS_PROCESSING]
                )[:50],
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
