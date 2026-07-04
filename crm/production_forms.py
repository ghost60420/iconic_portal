from django import forms
from .models import ProductionOrder, ProductionStage


PRODUCTION_INTERNAL_COST_FIELDS = {
    "fabric_cost_per_kg_bdt",
    "material_thread_cost_bdt",
    "material_zipper_cost_bdt",
    "material_accessories_cost_bdt",
    "material_label_cost_bdt",
    "material_other_cost_bdt",
    "production_cutting_cost_bdt",
    "production_sewing_cost_bdt",
    "production_finishing_cost_bdt",
    "production_packing_cost_bdt",
    "production_overhead_cost_bdt",
    "production_other_cost_bdt",
    "remake_cost_bdt",
}


class ProductionOrderForm(forms.ModelForm):
    sewing_start_date = forms.DateField(
        required=False,
        label="Start date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    sewing_end_date = forms.DateField(
        required=False,
        label="End date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model = ProductionOrder
        fields = [
            # basic info
            "title",
            "factory_location",
            "production_order_type",
            "operational_status",
            "order_type",
            "lead",
            "opportunity",
            "customer",
            "assigned_production_manager",
            "product",
            "sample_deadline",
            "bulk_deadline",
            "qty_total",
            "qty_reject",
            "sewing_charge_per_piece_bdt",
            "sewing_cost_per_piece_bdt",
            "extra_local_cost_bdt",
            "completed_quantity",

            # middle section
            "style_name",
            "color_info",
            "size_group",
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
        can_edit_internal_costing = kwargs.pop("can_edit_internal_costing", False)
        can_edit_local_sewing_financials = kwargs.pop(
            "can_edit_local_sewing_financials",
            can_edit_internal_costing,
        )
        super().__init__(*args, **kwargs)

        if not self.instance.pk and "order_type" in self.fields:
            self.fields["order_type"].choices = [
                choice
                for choice in self.fields["order_type"].choices
                if choice[0] != "sewing_charge"
            ]
            self.fields["order_type"].help_text = (
                "Bangladesh Local Sewing orders are created only from CEO-approved Quick Costing."
            )

        if not can_edit_internal_costing:
            for field_name in PRODUCTION_INTERNAL_COST_FIELDS:
                self.fields.pop(field_name, None)
        if not can_edit_local_sewing_financials:
            for field_name in (
                    "sewing_charge_per_piece_bdt",
                    "sewing_cost_per_piece_bdt",
                    "extra_local_cost_bdt",
                ):
                    self.fields.pop(field_name, None)

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

        if "completed_quantity" in self.fields:
            self.fields["completed_quantity"].required = False
            if not self.instance.pk:
                self.fields["completed_quantity"].initial = 0

        if self.instance.pk:
            sewing_stage = self.instance.stages.filter(stage_key="sewing").first()
            if sewing_stage:
                self.fields["sewing_start_date"].initial = sewing_stage.actual_start
                self.fields["sewing_end_date"].initial = sewing_stage.actual_end

        if "operational_status" in self.fields:
            self.fields["operational_status"].required = False
            if not self.instance.pk:
                self.fields["operational_status"].initial = "planning"

        if "production_order_type" in self.fields:
            self.fields["production_order_type"].required = True
            if not self.instance.pk:
                self.fields["production_order_type"].initial = "bulk"

        if "bulk_deadline" in self.fields:
            self.fields["bulk_deadline"].label = "Delivery date"

        if "assigned_production_manager" in self.fields:
            self.fields["assigned_production_manager"].label = "Assigned production manager"

        for field_name, placeholder in {
            "lead": "Search lead ID, brand, or contact",
            "opportunity": "Search opportunity ID or brand",
            "customer": "Search customer or contact",
            "assigned_production_manager": "Search production manager",
            "product": "Search product code or style",
        }.items():
            if field_name in self.fields:
                css = self.fields[field_name].widget.attrs.get("class", "")
                self.fields[field_name].widget.attrs["class"] = f"{css} crm-searchable-select".strip()
                self.fields[field_name].widget.attrs["data-search-placeholder"] = placeholder

        if "size_group" in self.fields:
            self.fields["size_group"].required = False
            self.fields["size_group"].initial = self.instance.size_group or "unisex"

        # helper text for middle section
        self.fields["style_name"].widget.attrs["placeholder"] = "Internal style name"
        self.fields["style_name"].widget.attrs["list"] = "production-style-suggestions"
        self.fields["color_info"].widget.attrs["placeholder"] = "Color list or code"
        self.fields["size_ratio_note"].widget.attrs["placeholder"] = "Size breakdown and ratio"
        self.fields["accessories_note"].widget.attrs["placeholder"] = "Zipper, buttons, drawcord, label plan"
        self.fields["packaging_note"].widget.attrs["placeholder"] = "Poly, carton, bundle, sticker"
        self.fields["extra_order_note"].widget.attrs["placeholder"] = "Anything extra for this order"

    def clean_size_group(self):
        value = (self.cleaned_data.get("size_group") or "unisex").strip().lower()
        valid_values = {key for key, _ in ProductionOrder.SIZE_GROUP_CHOICES}
        return value if value in valid_values else "unisex"

    def clean(self):
        cleaned = super().clean()
        is_local = (
            cleaned.get("order_type") == "sewing_charge"
            and cleaned.get("factory_location") == "bd"
        )
        if not is_local:
            return cleaned
        if not self.instance.pk:
            self.add_error(
                "order_type",
                "Bangladesh Local Sewing must be created from an approved Quick Costing.",
            )
            return cleaned

        quantity = cleaned.get("qty_total") or 0
        rejected = cleaned.get("qty_reject") or 0
        completed = cleaned.get("completed_quantity") or 0
        charge = cleaned.get(
            "sewing_charge_per_piece_bdt",
            getattr(self.instance, "sewing_charge_per_piece_bdt", None),
        )
        cost = cleaned.get(
            "sewing_cost_per_piece_bdt",
            getattr(self.instance, "sewing_cost_per_piece_bdt", None),
        )
        extra_cost = cleaned.get(
            "extra_local_cost_bdt",
            getattr(self.instance, "extra_local_cost_bdt", None),
        )
        start_date = cleaned.get("sewing_start_date")
        end_date = cleaned.get("sewing_end_date")

        if quantity <= 0:
            self.add_error("qty_total", "Quantity must be greater than zero for local sewing.")
        if charge is None or charge <= 0:
            if "sewing_charge_per_piece_bdt" in self.fields:
                self.add_error(
                    "sewing_charge_per_piece_bdt",
                    "Sewing charge per piece is required and must be greater than zero.",
                )
            else:
                self.add_error(None, "Finance must add the sewing charge before this order can be saved.")
        if cost is not None and cost < 0:
            if "sewing_cost_per_piece_bdt" in self.fields:
                self.add_error("sewing_cost_per_piece_bdt", "Sewing cost cannot be negative.")
            else:
                self.add_error(None, "The saved sewing cost is invalid. Finance must correct it.")
        if extra_cost is not None and extra_cost < 0:
            if "extra_local_cost_bdt" in self.fields:
                self.add_error("extra_local_cost_bdt", "Extra local cost cannot be negative.")
            else:
                self.add_error(None, "The saved extra local cost is invalid. Finance must correct it.")
        if completed > quantity:
            self.add_error("completed_quantity", "Completed quantity cannot exceed order quantity.")
        if rejected > quantity:
            self.add_error("qty_reject", "Rejected quantity cannot exceed order quantity.")
        if completed + rejected > quantity:
            self.add_error(
                "completed_quantity",
                "Completed and rejected quantities cannot exceed order quantity.",
            )
        if start_date and end_date and end_date < start_date:
            self.add_error("sewing_end_date", "End date cannot be before start date.")
        return cleaned

    def save(self, commit=True):
        was_adding = self.instance._state.adding
        order = super().save(commit=commit)
        if commit:
            from .services.production_operational_status import sync_operational_status

            explicit_status = self.cleaned_data.get("operational_status")
            if was_adding and explicit_status == "planning":
                sync_operational_status(order)
            else:
                sync_operational_status(order, explicit_status=explicit_status)
            if order.order_type == "sewing_charge" and order.factory_location == "bd":
                stage = order.stages.filter(stage_key="sewing").first()
                if stage is None:
                    stage = ProductionStage(order=order, stage_key="sewing", display_name="Sewing")
                stage.actual_start = self.cleaned_data.get("sewing_start_date")
                stage.actual_end = self.cleaned_data.get("sewing_end_date")
                stage.save()
        return order


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
