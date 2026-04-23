import os

from django import forms

from .models import LeadBrainCompany, LeadBrainDiscoveryJob, LeadBrainUpload
from .services.discovery_service import (
    DISCOVERY_MAX_JOBS_PER_DAY,
    DISCOVERY_MAX_RESULTS,
    DISCOVERY_MIN_RESULTS,
    normalized_max_results,
    normalized_min_fit_score,
)


class LeadBrainUploadForm(forms.ModelForm):
    class Meta:
        model = LeadBrainUpload
        fields = ["file"]
        widgets = {
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        ext = os.path.splitext(uploaded_file.name or "")[1].lower()
        if ext not in {".xlsx", ".xls", ".csv"}:
            raise forms.ValidationError("Please upload a CSV, XLSX, or XLS file.")
        return uploaded_file


class LeadBrainCompanyNotesForm(forms.ModelForm):
    class Meta:
        model = LeadBrainCompany
        fields = ["notes", "reviewed"]
        widgets = {
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "reviewed": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class LeadBrainDiscoveryJobForm(forms.ModelForm):
    selected_sources = forms.MultipleChoiceField(
        choices=LeadBrainDiscoveryJob.SOURCE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )

    class Meta:
        model = LeadBrainDiscoveryJob
        fields = [
            "name",
            "country",
            "niche",
            "schedule_type",
            "run_time",
            "max_results",
            "max_runs_per_day",
            "apparel_only",
            "minimum_score",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Canada streetwear discovery"}),
            "country": forms.Select(attrs={"class": "form-select"}),
            "niche": forms.Select(attrs={"class": "form-select"}),
            "schedule_type": forms.Select(attrs={"class": "form-select"}),
            "run_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "max_results": forms.NumberInput(
                attrs={"class": "form-control", "min": DISCOVERY_MIN_RESULTS, "max": DISCOVERY_MAX_RESULTS}
            ),
            "max_runs_per_day": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": DISCOVERY_MAX_JOBS_PER_DAY}
            ),
            "apparel_only": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "minimum_score": forms.NumberInput(attrs={"class": "form-control", "min": 65, "max": 100}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_sources"].initial = self.instance.selected_sources if getattr(self.instance, "pk", None) else [LeadBrainDiscoveryJob.SOURCE_WEB]
        self.fields["name"].required = True

    def clean_max_results(self):
        value = self.cleaned_data["max_results"]
        return normalized_max_results(value)

    def clean_max_runs_per_day(self):
        value = int(self.cleaned_data.get("max_runs_per_day") or 1)
        return max(1, min(value, DISCOVERY_MAX_JOBS_PER_DAY))

    def clean_minimum_score(self):
        return normalized_min_fit_score(self.cleaned_data.get("minimum_score") or 65)

    def clean_selected_sources(self):
        values = [value for value in self.cleaned_data.get("selected_sources", []) if value]
        if not values:
            raise forms.ValidationError("Choose at least one discovery source.")
        return values

    def save(self, commit=True):
        job = super().save(commit=False)
        sources = self.cleaned_data["selected_sources"]
        job.selected_sources_json = sources
        job.source_types_json = sources
        job.source_type = sources[0]
        job.countries_json = [job.country] if job.country else []
        job.niches_json = [job.niche] if job.niche else []
        job.max_results = normalized_max_results(job.max_results)
        job.max_results_per_run = job.max_results
        job.minimum_score = normalized_min_fit_score(job.minimum_score)
        job.min_fit_score = job.minimum_score
        job.is_active = not job.is_paused
        if not job.name:
            job.name = f"{job.country} {job.get_niche_display()} Discovery"
        if job.schedule_type == LeadBrainDiscoveryJob.SCHEDULE_MANUAL:
            job.next_run_at = None
        elif not job.next_run_at:
            job.next_run_at = job.compute_next_run_at()
        if commit:
            job.save()
        return job
