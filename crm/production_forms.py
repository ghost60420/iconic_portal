from django import forms
from .models import ProductionOrder, ProductionStage


class ProductionOrderForm(forms.ModelForm):
    class Meta:
        model = ProductionOrder
        fields = [
            # basic info
            "title",
            "factory_location",
            "order_type",
            "lead",
            "opportunity",
            "customer",
            "product",
            "sample_deadline",
            "bulk_deadline",
            "qty_total",
            "qty_reject",

            # middle section
            "style_name",
            "color_info",
            "size_ratio_note",
            "accessories_note",
            "packaging_note",
            "extra_order_note",

            # fabric
            "fabric_required_kg",
            "fabric_received_kg",
            "fabric_used_kg",
            "fabric_cost_per_kg_bdt",

            # material
            "material_thread_cost_bdt",
            "material_zipper_cost_bdt",
            "material_accessories_cost_bdt",
            "material_label_cost_bdt",
            "material_other_cost_bdt",

            # production
            "production_cutting_cost_bdt",
            "production_sewing_cost_bdt",
            "production_finishing_cost_bdt",
            "production_packing_cost_bdt",
            "production_overhead_cost_bdt",
            "production_other_cost_bdt",

            # remake
            "remake_required",
            "remake_qty",
            "remake_cost_bdt",

            # other
            "status",
            "style_image",
            "notes",
        ]
        widgets = {
            "sample_deadline": forms.DateInput(attrs={"type": "date"}),
            "bulk_deadline": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "size_ratio_note": forms.Textarea(attrs={"rows": 2}),
            "accessories_note": forms.Textarea(attrs={"rows": 2}),
            "packaging_note": forms.Textarea(attrs={"rows": 2}),
            "extra_order_note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # dark style for all fields
        for name, field in self.fields.items():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (
                css
                + " form-control form-control-sm bg-dark text-light border-secondary"
            ).strip()

        # checkbox normal look
        if "remake_required" in self.fields:
            self.fields["remake_required"].widget.attrs["class"] = ""

        # not required defaults
        if "qty_reject" in self.fields:
            self.fields["qty_reject"].required = False
            if not self.instance.pk:
                self.fields["qty_reject"].initial = 0

        if "status" in self.fields:
            self.fields["status"].required = False
            if not self.instance.pk:
                self.fields["status"].initial = "planning"

        # helper text for middle section
        self.fields["style_name"].widget.attrs["placeholder"] = "Internal style name"
        self.fields["color_info"].widget.attrs["placeholder"] = "Color list or code"
        self.fields["size_ratio_note"].widget.attrs["placeholder"] = "Size breakdown and ratio"
        self.fields["accessories_note"].widget.attrs["placeholder"] = "Zipper, buttons, drawcord, label plan"
        self.fields["packaging_note"].widget.attrs["placeholder"] = "Poly, carton, bundle, sticker"
        self.fields["extra_order_note"].widget.attrs["placeholder"] = "Anything extra for this order"


class ProductionStageForm(forms.ModelForm):
    class Meta:
        model = ProductionStage
        fields = [
            "status",
            "planned_start",
            "planned_end",
            "actual_start",
            "actual_end",
            "notes",
        ]
        widgets = {
            "status": forms.Select(
                attrs={"class": "form-select form-select-sm bg-dark text-light border-secondary"}
            ),
            "planned_start": forms.DateInput(
                attrs={
                    "class": "form-control form-control-sm bg-dark text-light border-secondary",
                    "type": "date",
                }
            ),
            "planned_end": forms.DateInput(
                attrs={
                    "class": "form-control form-control-sm bg-dark text-light border-secondary",
                    "type": "date",
                }
            ),
            "actual_start": forms.DateInput(
                attrs={
                    "class": "form-control form-control-sm bg-dark text-light border-secondary",
                    "type": "date",
                }
            ),
            "actual_end": forms.DateInput(
                attrs={
                    "class": "form-control form-control-sm bg-dark text-light border-secondary",
                    "type": "date",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control form-control-sm bg-dark text-light border-secondary",
                    "rows": 3,
                }
            ),
        }