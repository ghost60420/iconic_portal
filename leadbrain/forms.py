import os

from django import forms

from .models import LeadBrainCompany, LeadBrainUpload


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

