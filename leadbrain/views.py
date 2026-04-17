import json
import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from .forms import LeadBrainCompanyNotesForm, LeadBrainUploadForm
from .models import LeadBrainCompany, LeadBrainUpload
from .services.classification_service import classify_company
from .services.file_parser import parse_uploaded_file
from .services.research_service import research_company


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

        upload = form.save(commit=False)
        upload.uploaded_by = request.user
        upload.file_name = os.path.basename(upload.file.name or "")
        upload.status = LeadBrainUpload.STATUS_PROCESSING
        upload.save()

        try:
            rows = parse_uploaded_file(upload.file.path)
        except Exception as exc:
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.save(update_fields=["status", "updated_at"])
            form.add_error("file", str(exc))
            messages.error(request, "The upload could not be processed.")
            return render(request, self.template_name, {"form": form})

        upload.row_count = len(rows)
        upload.save(update_fields=["row_count", "updated_at"])

        try:
            created_count = 0
            for row in rows:
                company = None
                try:
                    company = LeadBrainCompany.objects.create(
                        upload=upload,
                        row_number=row.get("row_number", 0),
                        company_name=row.get("company_name", ""),
                        website=row.get("website", ""),
                        email=row.get("email", ""),
                        phone=row.get("phone", ""),
                        country=row.get("country", ""),
                        city=row.get("city", ""),
                        raw_row_json=row.get("raw_row_json", {}),
                        fit_label=LeadBrainCompany.FIT_WEAK,
                    )
                    created_count += 1

                    research_data = research_company(company)
                    classification = classify_company(company, research_data)

                    company.website = (company.website or research_data.get("official_website_found", ""))[:200]
                    company.email = company.email or research_data.get("public_email_found", "")
                    company.phone = company.phone or research_data.get("public_phone_found", "")
                    company.linkedin_url = research_data.get("linkedin_url_found", "")[:200]
                    company.best_contact_name = research_data.get("possible_contact_name", "")
                    company.best_contact_title = classification.get("best_contact_title", "")
                    company.business_type = classification.get("business_type", "")
                    company.fit_label = classification.get("fit_label", LeadBrainCompany.FIT_WEAK)
                    company.fit_score = classification.get("fit_score", 0)
                    company.ai_summary = classification.get("ai_summary", "")
                    company.fit_reason = classification.get("fit_reason", "")
                    company.suggested_action = classification.get("suggested_action", "")
                    company.research_json = research_data
                    company.save()
                except Exception as exc:
                    if company is None:
                        continue
                    company.fit_label = LeadBrainCompany.FIT_WEAK
                    company.fit_score = 0
                    company.ai_summary = "Research could not be completed for this row."
                    company.fit_reason = "Partial data was saved, but the row needs manual review."
                    company.suggested_action = "Review Manually"
                    company.research_json = {
                        "website_status": "failed",
                        "official_website_found": "",
                        "linkedin_url_found": "",
                        "public_email_found": "",
                        "public_phone_found": "",
                        "business_description": "",
                        "apparel_signals": [],
                        "search_summary": "",
                        "possible_contact_name": "",
                        "possible_contact_title": "",
                        "confidence_notes": f"Row processing error: {exc}",
                    }
                    company.save()

            upload.status = LeadBrainUpload.STATUS_COMPLETE if created_count else LeadBrainUpload.STATUS_FAILED
            upload.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Upload processed. {created_count} company row(s) were saved.")
            return redirect(f"{reverse_lazy('leadbrain_results')}?upload={upload.pk}")
        except Exception:
            upload.status = LeadBrainUpload.STATUS_FAILED
            upload.save(update_fields=["status", "updated_at"])
            messages.error(request, "The upload failed before the full process could complete.")
            return redirect("leadbrain_upload")


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

        context.update(
            {
                "page_obj": page_obj,
                "companies": page_obj.object_list,
                "fit_label": fit_label,
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
