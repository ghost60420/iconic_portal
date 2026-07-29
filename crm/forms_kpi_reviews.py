from datetime import date
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from crm.models_employee import EmployeeProfile
from crm.models_kpi_reviews import KPIReview


class KPIReviewCreateForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=EmployeeProfile.objects.none(),
        empty_label="Select employee",
    )
    period_type = forms.ChoiceField(choices=KPIReview.PERIOD_CHOICES)
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    review_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, employee_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = (
            employee_queryset
            if employee_queryset is not None
            else EmployeeProfile.objects.none()
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "kpi-input"

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("period_start")
        end = cleaned.get("period_end")
        review_date = cleaned.get("review_date")
        if start and end and end < start:
            self.add_error("period_end", "Period end cannot be before period start.")
        if start and end and review_date and not start <= review_date <= end:
            self.add_error(
                "review_date",
                "Review date must fall inside the selected period.",
            )
        return cleaned


class KPIReviewEntryForm(forms.Form):
    manager_comment = forms.CharField(
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "kpi-input",
                "placeholder": "Manager review comment",
            }
        ),
    )

    def __init__(self, *args, review, entries, **kwargs):
        super().__init__(*args, **kwargs)
        self.review = review
        self.entries = list(entries)
        definition_items = {}
        for role in review.definition_snapshot.get("roles", []):
            for item in role.get("items", []):
                definition_items[
                    (role["assignment_id"], item["kpi_item_id"])
                ] = (role, item)

        self.entry_rows = []
        for entry in self.entries:
            role, item = definition_items[(entry.assignment_id, entry.kpi_item_id)]
            actual_name = f"actual_{entry.pk}"
            critical_name = f"critical_{entry.pk}"
            reason_name = f"critical_reason_{entry.pk}"
            trigger_name = f"critical_trigger_{entry.pk}"
            comment_name = f"comment_{entry.pk}"

            if item["measurement"]["measurement_type"] == "boolean":
                self.fields[actual_name] = forms.TypedChoiceField(
                    required=False,
                    choices=(("", "Not entered"), ("1", "Yes"), ("0", "No")),
                    coerce=lambda value: Decimal(value) if value != "" else None,
                    empty_value=None,
                    initial=(
                        str(int(entry.actual_value))
                        if entry.actual_value is not None
                        else ""
                    ),
                    widget=forms.Select(attrs={"class": "kpi-input"}),
                )
            else:
                self.fields[actual_name] = forms.DecimalField(
                    required=False,
                    max_digits=20,
                    decimal_places=6,
                    initial=entry.actual_value,
                    widget=forms.NumberInput(
                        attrs={
                            "class": "kpi-input",
                            "step": "0.000001",
                        }
                    ),
                )
            self.fields[critical_name] = forms.BooleanField(
                required=False,
                initial=entry.critical_red,
                widget=forms.CheckboxInput(attrs={"class": "kpi-checkbox"}),
            )
            self.fields[reason_name] = forms.CharField(
                required=False,
                max_length=4000,
                initial=entry.critical_reason,
                widget=forms.Textarea(
                    attrs={
                        "class": "kpi-input",
                        "rows": 2,
                        "placeholder": "Critical Red reason",
                    }
                ),
            )
            self.fields[trigger_name] = forms.CharField(
                required=False,
                max_length=160,
                initial=entry.critical_trigger,
                widget=forms.TextInput(
                    attrs={
                        "class": "kpi-input",
                        "placeholder": "Critical Red trigger",
                    }
                ),
            )
            self.fields[comment_name] = forms.CharField(
                required=False,
                max_length=4000,
                initial=entry.item_comment,
                widget=forms.Textarea(
                    attrs={
                        "class": "kpi-input",
                        "rows": 2,
                        "placeholder": "Item comment",
                    }
                ),
            )
            self.entry_rows.append(
                {
                    "entry": entry,
                    "role": role,
                    "item": item,
                    "actual": self[actual_name],
                    "critical": self[critical_name],
                    "critical_reason": self[reason_name],
                    "critical_trigger": self[trigger_name],
                    "comment": self[comment_name],
                }
            )
        self.fields["manager_comment"].initial = review.manager_comment

    def clean(self):
        cleaned = super().clean()
        for entry in self.entries:
            critical_name = f"critical_{entry.pk}"
            reason_name = f"critical_reason_{entry.pk}"
            trigger_name = f"critical_trigger_{entry.pk}"
            if cleaned.get(critical_name):
                if not (cleaned.get(reason_name) or "").strip():
                    self.add_error(reason_name, "Critical Red requires a reason.")
                if not (cleaned.get(trigger_name) or "").strip():
                    self.add_error(trigger_name, "Critical Red requires a trigger.")
        return cleaned

    def entry_values(self):
        if not self.is_valid():
            raise ValidationError("Review form must be valid before reading values.")
        return {
            entry.pk: {
                "actual_value": self.cleaned_data.get(f"actual_{entry.pk}"),
                "critical_red": self.cleaned_data.get(
                    f"critical_{entry.pk}", False
                ),
                "critical_reason": self.cleaned_data.get(
                    f"critical_reason_{entry.pk}", ""
                ),
                "critical_trigger": self.cleaned_data.get(
                    f"critical_trigger_{entry.pk}", ""
                ),
                "item_comment": self.cleaned_data.get(
                    f"comment_{entry.pk}", ""
                ),
            }
            for entry in self.entries
        }


class KPIReviewDecisionForm(forms.Form):
    comment = forms.CharField(
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "kpi-input",
                "placeholder": "Decision comment",
            }
        ),
    )


class KPIReviewRejectForm(KPIReviewDecisionForm):
    comment = forms.CharField(
        required=True,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "kpi-input",
                "placeholder": "Required correction and rejection reason",
            }
        ),
    )
