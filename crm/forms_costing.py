from decimal import Decimal
from django import forms

from .models import (
    ActualCostEntry,
    CostLineItem,
    CostSheet,
    CostSheetSimple,
    Opportunity,
    OpportunityDocument,
)


def _safe_opportunity_label(opportunity):
    label = opportunity.opportunity_id or f"Opportunity {opportunity.pk}"
    try:
        brand = opportunity.lead.account_brand
    except Exception:
        brand = ""
    return f"{label} - {brand}" if brand else label


class CostSheetSimpleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "opportunity" in self.fields:
            self.fields["opportunity"].label_from_instance = _safe_opportunity_label
        # Cost inputs default to zero; make them optional for create form.
        for name in self._cost_defaults().keys():
            if name in self.fields:
                self.fields[name].required = False

    @staticmethod
    def _cost_defaults():
        return {
            "fabric_cost_per_piece": Decimal("0"),
            "fabric_wastage_percent": Decimal("0"),
            "rib_cost_per_piece": Decimal("0"),
            "woven_fabric_cost_per_piece": Decimal("0"),
            "zipper_cost_per_piece": Decimal("0"),
            "zipper_puller_cost_per_piece": Decimal("0"),
            "button_cost_per_piece": Decimal("0"),
            "thread_cost_per_piece": Decimal("0"),
            "lining_cost_per_piece": Decimal("0"),
            "velcro_cost_per_piece": Decimal("0"),
            "neck_tape_cost_per_piece": Decimal("0"),
            "elastic_cost_per_piece": Decimal("0"),
            "collar_cuff_cost_per_piece": Decimal("0"),
            "ring_cost_per_piece": Decimal("0"),
            "buckle_clip_cost_per_piece": Decimal("0"),
            "main_label_cost_per_piece": Decimal("0"),
            "care_label_cost_per_piece": Decimal("0"),
            "hang_tag_cost_per_piece": Decimal("0"),
            "conveyance_cost_per_piece": Decimal("0"),
            "trim_cost_per_piece": Decimal("0"),
            "labor_cost_per_piece": Decimal("0"),
            "overhead_cost_per_piece": Decimal("0"),
            "process_cost_per_piece": Decimal("0"),
            "packaging_cost_per_piece": Decimal("0"),
            "freight_cost_per_piece": Decimal("0"),
            "testing_cost_per_piece": Decimal("0"),
            "other_cost_per_piece": Decimal("0"),
            "quote_price_per_piece": Decimal("0"),
        }

    def clean(self):
        cleaned = super().clean()
        defaults = self._cost_defaults()
        for name, default in defaults.items():
            if cleaned.get(name) in [None, ""]:
                cleaned[name] = default
        return cleaned

    class Meta:
        model = CostSheetSimple
        fields = [
            "opportunity",
            "customer",
            "style_name",
            "style_code",
            "product_type",
            "quantity",
            "factory_location",
            "exchange_rate_bdt_per_cad",
            "fabric_cost_per_piece",
            "fabric_wastage_percent",
            "rib_cost_per_piece",
            "woven_fabric_cost_per_piece",
            "zipper_cost_per_piece",
            "zipper_puller_cost_per_piece",
            "button_cost_per_piece",
            "thread_cost_per_piece",
            "lining_cost_per_piece",
            "velcro_cost_per_piece",
            "neck_tape_cost_per_piece",
            "elastic_cost_per_piece",
            "collar_cuff_cost_per_piece",
            "ring_cost_per_piece",
            "buckle_clip_cost_per_piece",
            "main_label_cost_per_piece",
            "care_label_cost_per_piece",
            "hang_tag_cost_per_piece",
            "conveyance_cost_per_piece",
            "trim_cost_per_piece",
            "labor_cost_per_piece",
            "overhead_cost_per_piece",
            "process_cost_per_piece",
            "packaging_cost_per_piece",
            "freight_cost_per_piece",
            "testing_cost_per_piece",
            "other_cost_per_piece",
            "quote_price_per_piece",
            "notes",
        ]
        widgets = {
            "style_name": forms.TextInput(attrs={"placeholder": "Style name"}),
            "style_code": forms.TextInput(attrs={"placeholder": "Style code"}),
            "quantity": forms.NumberInput(attrs={"min": 0, "step": "1", "placeholder": "1000"}),
            "exchange_rate_bdt_per_cad": forms.NumberInput(
                attrs={"step": "0.01", "placeholder": "140.00"}
            ),
            "fabric_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "fabric_wastage_percent": forms.NumberInput(attrs={"step": "0.01", "placeholder": "2"}),
            "rib_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "woven_fabric_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "zipper_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "zipper_puller_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "button_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "thread_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "lining_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "velcro_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "neck_tape_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "elastic_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "collar_cuff_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "ring_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "buckle_clip_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "main_label_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "care_label_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "hang_tag_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "conveyance_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "trim_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "labor_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "overhead_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "process_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "packaging_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "freight_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "testing_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "other_cost_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "quote_price_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Assumptions and remarks"}),
        }
        labels = {
            "exchange_rate_bdt_per_cad": "Exchange rate (\u09F3 per 1 CAD)",
        }


class CostSheetForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "opportunity" in self.fields:
            self.fields["opportunity"].label_from_instance = _safe_opportunity_label

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
            "style_code": forms.TextInput(attrs={"placeholder": "Style code"}),
            "style_name": forms.TextInput(attrs={"placeholder": "Style name"}),
            "target_quantity": forms.NumberInput(attrs={"min": 0, "step": "1", "placeholder": "1000"}),
            "target_margin_percent": forms.NumberInput(attrs={"step": "0.01", "placeholder": "35"}),
            "quote_price_per_piece": forms.NumberInput(
                attrs={"step": "0.0001", "placeholder": "Leave blank to auto-calc"}
            ),
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
            "item_name": forms.TextInput(attrs={"placeholder": "Main fabric"}),
            "uom": forms.TextInput(attrs={"placeholder": "kg, pc, min"}),
            "consumption_per_piece": forms.NumberInput(attrs={"step": "0.0001", "placeholder": "0.65"}),
            "waste_percent": forms.NumberInput(attrs={"step": "0.01", "placeholder": "2"}),
            "rate": forms.NumberInput(attrs={"step": "0.0001", "placeholder": "5.50"}),
            "setup_cost": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0"}),
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
