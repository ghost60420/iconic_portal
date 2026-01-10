from django import forms
from .models_access import UserAccess

class UserAccessForm(forms.ModelForm):
    class Meta:
        model = UserAccess
        fields = [
            "can_leads",
            "can_opportunities",
            "can_customers",
            "can_inventory",
            "can_production",
            "can_shipping",
            "can_ai",
            "can_calendar",
            "can_accounting_bd",
            "can_accounting_ca",
        ]