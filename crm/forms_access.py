# crm/forms_access.py

from django import forms
from .models_access import UserAccess


class UserAccessForm(forms.ModelForm):
    class Meta:
        model = UserAccess
        fields = [
            "role",
            "can_leads",
            "can_opportunities",
            "can_customers",
            "can_inventory",
            "can_production",
            "can_shipping",
            "can_ai",
            "can_calendar",
            "can_marketing",
            "can_whatsapp",
            "can_costing",
            "can_costing_approve",
            "can_accounting_bd",
            "can_accounting_ca",
        ]
        widgets = {
            "role": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "role": "Team",
            "can_leads": "Leads",
            "can_opportunities": "Opportunities",
            "can_customers": "Customers",
            "can_inventory": "Inventory",
            "can_production": "Production",
            "can_shipping": "Shipping",
            "can_ai": "AI",
            "can_calendar": "Calendar",
            "can_marketing": "Marketing",
            "can_whatsapp": "WhatsApp",
            "can_costing": "Costing",
            "can_costing_approve": "Costing approve/lock",
            "can_accounting_bd": "Accounting BD",
            "can_accounting_ca": "Accounting CA",
        }
        help_texts = {
            "can_accounting_ca": "CA accounting is never allowed for BD users.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Add Bootstrap checkbox class to all boolean fields
        for name, field in self.fields.items():
            if name == "role":
                continue
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (existing + " form-check-input").strip()

        # Pick role from posted data first (so it updates on submit),
        # otherwise use instance role
        role_val = self.data.get("role") or getattr(self.instance, "role", None)

        # If BD, do not allow CA accounting checkbox interaction
        if role_val == UserAccess.ROLE_BD:
            self.fields["can_accounting_ca"].disabled = True

    def clean(self):
        cleaned = super().clean()

        # Hard rule: BD cannot have CA accounting
        if cleaned.get("role") == UserAccess.ROLE_BD:
            cleaned["can_accounting_ca"] = False

        return cleaned
