from django import forms

from .models import ActualCostEntry, CostLineItem, CostSheet, OpportunityDocument


class CostSheetForm(forms.ModelForm):
    class Meta:
        model = CostSheet
        fields = [
            "opportunity",
            "customer",
            "product_type",
            "style_code",
            "style_name",
            "currency",
            "production_location",
            "target_quantity",
            "overhead_method",
            "target_margin_percent",
            "quote_price_per_piece",
        ]
        widgets = {
            "target_quantity": forms.NumberInput(attrs={"min": 0, "step": "1"}),
            "target_margin_percent": forms.NumberInput(attrs={"step": "0.01"}),
            "quote_price_per_piece": forms.NumberInput(attrs={"step": "0.0001"}),
        }


class CostLineItemForm(forms.ModelForm):
    class Meta:
        model = CostLineItem
        fields = [
            "section",
            "item_name",
            "uom",
            "consumption_per_piece",
            "waste_percent",
            "rate",
            "setup_cost",
            "notes",
        ]
        widgets = {
            "consumption_per_piece": forms.NumberInput(attrs={"step": "0.0001"}),
            "waste_percent": forms.NumberInput(attrs={"step": "0.01"}),
            "rate": forms.NumberInput(attrs={"step": "0.0001"}),
            "setup_cost": forms.NumberInput(attrs={"step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 1}),
        }


class OpportunityDocumentForm(forms.ModelForm):
    class Meta:
        model = OpportunityDocument
        fields = [
            "file",
            "doc_type",
        ]


class ActualCostEntryForm(forms.ModelForm):
    class Meta:
        model = ActualCostEntry
        fields = [
            "section",
            "item_name",
            "uom",
            "actual_qty_total",
            "actual_rate",
            "actual_total_cost",
            "notes",
        ]
        widgets = {
            "actual_qty_total": forms.NumberInput(attrs={"step": "0.0001"}),
            "actual_rate": forms.NumberInput(attrs={"step": "0.0001"}),
            "actual_total_cost": forms.NumberInput(attrs={"step": "0.0001"}),
            "notes": forms.Textarea(attrs={"rows": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "actual_total_cost" in self.fields:
            self.fields["actual_total_cost"].required = False
