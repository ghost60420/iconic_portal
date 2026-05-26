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
            "can_view_internal_costing",
            "can_costing_approve",
            "can_view_ceo_tools",
            "can_accounting_bd",
            "can_accounting_ca",
            "can_library",
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
            "can_view_internal_costing": "Internal costing",
            "can_costing_approve": "Costing approve/lock",
            "can_view_ceo_tools": "CEO tools",
            "can_accounting_bd": "Accounting BD",
            "can_accounting_ca": "Accounting CA",
            "can_library": "Library",
        }
        help_texts = {
            "can_accounting_ca": "CA accounting is never allowed for BD users.",
            "can_view_internal_costing": "Allows viewing costing profit, margin, internal costs, and lifecycle profit metrics.",
            "can_view_ceo_tools": "Restricts CEO Dashboard, AI Executive Advisor, and Daily Briefing access.",
        }

    def __init__(self, *args, **kwargs):
        can_manage_ceo_tools = kwargs.pop("can_manage_ceo_tools", False)
        super().__init__(*args, **kwargs)

        # Add Bootstrap checkbox class to all boolean fields
        for name, field in self.fields.items():
            if name == "role":
                continue
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (existing + " form-check-input").strip()

        if "role" in self.fields:
            self.fields["role"].widget.attrs["data-role-select"] = "1"

        if "can_accounting_ca" in self.fields:
            self.fields["can_accounting_ca"].widget.attrs["data-ca-checkbox"] = "1"

        if "can_view_ceo_tools" in self.fields and not can_manage_ceo_tools:
            self.fields["can_view_ceo_tools"].disabled = True
            self.fields["can_view_ceo_tools"].help_text = "Only superusers can grant or remove CEO tools access."

        # Pick role from posted data first (so it updates on submit),
        # otherwise use instance role
        role_val = self.data.get(self.add_prefix("role")) or getattr(self.instance, "role", None)

        # If BD, do not allow CA accounting checkbox interaction
        if role_val == UserAccess.ROLE_BD:
            self.fields["can_accounting_ca"].disabled = True

    def clean(self):
        cleaned = super().clean()

        # Hard rule: BD cannot have CA accounting
        if cleaned.get("role") == UserAccess.ROLE_BD:
            cleaned["can_accounting_ca"] = False

        return cleaned
