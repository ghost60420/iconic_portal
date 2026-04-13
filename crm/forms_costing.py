from django import forms

from .models import (
    ActualCostEntry,
    CostLineItem,
    CostSheet,
    CostSheetSimple,
    CostingHeader,
    CostingSMV,
    Opportunity,
    OpportunityDocument,
)


COMMON_COSTING_SUGGESTIONS = {
    "buyer": [
        "Menswear",
        "Womenswear",
        "Kids",
        "Basics",
        "Outerwear",
        "Activewear",
    ],
    "brand": [
        "Private label",
        "House brand",
        "Licensed brand",
        "Retail program",
    ],
    "gender": [
        "Women",
        "Men",
        "Unisex",
        "Girls",
        "Boys",
        "Baby",
        "Toddler",
        "Kids",
    ],
    "size_range": [
        "XS-XL",
        "S-XXL",
        "2-8Y",
        "4-14Y",
        "NB-24M",
        "One size",
    ],
    "season": [
        "Spring/Summer",
        "Fall/Winter",
        "Holiday",
        "Core",
        "Back to School",
        "Resort",
    ],
    "merchandiser": [
        "In-house",
        "Customer merchandiser",
        "Sales team",
    ],
    "fabric_type": [
        "Single jersey",
        "Interlock",
        "Rib",
        "Fleece",
        "French terry",
        "Pique",
        "Woven poplin",
        "Denim",
        "Canvas",
        "Polar fleece",
    ],
    "fabric_gsm": [
        "160 GSM",
        "180 GSM",
        "200 GSM",
        "220 GSM",
        "240 GSM",
        "280 GSM",
        "320 GSM",
    ],
    "fabric_composition": [
        "100% cotton",
        "95% cotton / 5% spandex",
        "60% cotton / 40% polyester",
        "80% cotton / 20% polyester",
        "100% polyester",
        "CVC",
    ],
    "wash_type": [
        "None",
        "Soft wash",
        "Enzyme wash",
        "Garment wash",
        "Silicone wash",
        "Acid wash",
        "Pigment wash",
    ],
    "print_type": [
        "None",
        "Screen print",
        "Puff print",
        "Heat transfer",
        "DTG",
        "Sublimation",
        "Emboss print",
    ],
    "embroidery": [
        "None",
        "Flat embroidery",
        "3D embroidery",
        "Applique embroidery",
        "Badge embroidery",
    ],
    "label_type": [
        "Main label",
        "Care label",
        "Heat transfer label",
        "Woven main and care label",
        "Printed satin label",
    ],
    "packaging_type": [
        "Polybag only",
        "Polybag and carton",
        "Polybag, sticker and carton",
        "Hanger pack",
        "Flat pack",
    ],
    "special_trims": [
        "None",
        "Zipper",
        "Drawcord",
        "Toggle",
        "Patch",
        "Snap button",
        "Velcro",
        "Badge",
    ],
}


COMPREHENSIVE_COSTING_DROPDOWNS = {
    "fabric_type": [
        "Single jersey",
        "Interlock",
        "Rib",
        "Fleece",
        "French terry",
        "Pique",
        "Woven poplin",
        "Oxford",
        "Twill",
        "Denim",
        "Canvas",
        "Polar fleece",
        "Softshell",
        "Sherpa",
        "Mesh",
    ],
    "wash_type": [
        "None",
        "Soft wash",
        "Enzyme wash",
        "Garment wash",
        "Silicone wash",
        "Pigment wash",
        "Acid wash",
        "Stone wash",
        "Snow wash",
        "Bio polish",
        "Peach finish",
    ],
    "print_type": [
        "None",
        "Screen print",
        "Puff print",
        "High-density print",
        "Heat transfer",
        "DTG",
        "Sublimation",
        "Foil print",
        "Rubber print",
        "Plastisol print",
        "Emboss print",
    ],
    "embroidery": [
        "None",
        "Flat embroidery",
        "3D embroidery",
        "Applique embroidery",
        "Badge embroidery",
        "Chenille embroidery",
        "Sequin embroidery",
        "Placement logo embroidery",
        "Multi-location embroidery",
    ],
    "label_type": [
        "Main woven label",
        "Care label",
        "Size label",
        "Heat transfer label",
        "Printed satin label",
        "Woven main and care label",
        "Inside neck print",
        "Brand patch label",
    ],
    "packaging_type": [
        "Polybag only",
        "Polybag and carton",
        "Polybag, sticker and carton",
        "Flat pack",
        "Hanger pack",
        "Folded with belly band",
        "Gift box pack",
        "Retail-ready barcode pack",
        "Vacuum pack",
    ],
    "special_trims": [
        "None",
        "Zipper",
        "Snap button",
        "Button",
        "Drawcord",
        "Toggle",
        "Elastic cord",
        "Velcro",
        "Patch",
        "Badge",
        "Reflective tape",
        "Eyelet",
        "Cord end",
    ],
}


HEADER_HELP_TEXTS = {
    "buyer": "Internal buyer division or customer buying team for this style.",
    "brand": "Brand or label this product will be sold under.",
    "gender": "Choose the target consumer group for the product.",
    "size_range": "Enter the size set the quote covers, for example XS-XL or 2-8Y.",
    "season": "Commercial season or program this style belongs to.",
    "factory_location": "Use the production country that will build this costing.",
    "order_quantity": "Planned order quantity for the quotation. Totals are based on this number.",
    "moq": "Minimum order quantity expected or accepted for the style.",
    "costing_date": "Date this costing was prepared or revised.",
    "merchandiser": "Owner responsible for follow-up and commercial coordination.",
    "currency": "Working currency for the sheet header and quote logic.",
    "exchange_rate": "Only needed when the costing references a foreign quote or conversion.",
    "finance_percent_fabric": "Finance/loading percentage applied on fabric cost.",
    "finance_percent_trims": "Finance/loading percentage applied on trims cost.",
    "commission_percent": "Sales or agent commission percentage added on top of FOB.",
    "target_margin_percent": "If manual FOB is blank, system can back-calculate FOB from this margin target.",
    "manual_fob_per_piece": "Optional manual selling FOB per piece. Leave blank to auto-calculate from margin target.",
    "fabric_type": "Main body fabric construction used for the garment.",
    "fabric_gsm": "Fabric weight reference from tech pack or supplier quote.",
    "fabric_composition": "Fiber content used for costing and supplier matching.",
    "wash_type": "Any garment wash or finishing process affecting cost.",
    "print_type": "Print method used on the garment. Select none if not applicable.",
    "embroidery": "Embroidery method or indicate none.",
    "label_type": "Main branding and care label setup for the style.",
    "packaging_type": "How the garment is packed for shipment.",
    "special_trims": "List major trims that meaningfully change cost.",
    "fit_remarks": "Construction, fit, or workmanship notes that affect labor or trim choices.",
    "notes": "Assumptions, exclusions, or commercial remarks for the costing.",
}


def _safe_opportunity_label(opportunity):
    label = opportunity.opportunity_id or f"Opportunity {opportunity.pk}"
    try:
        brand = opportunity.lead.account_brand
    except Exception:
        brand = ""
    return f"{label} - {brand}" if brand else label


class CostingHeaderForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "opportunity" in self.fields:
            self.fields["opportunity"].label_from_instance = _safe_opportunity_label
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " costing-input").strip()
        for name, choices in COMPREHENSIVE_COSTING_DROPDOWNS.items():
            field = self.fields.get(name)
            if not field:
                continue
            field.widget = forms.Select(
                choices=[("", f"Select {field.label or name.replace('_', ' ')}")] + [(value, value) for value in choices]
            )
            field.required = False
            field.choices = [("", f"Select {field.label or name.replace('_', ' ')}")] + [(value, value) for value in choices]
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " costing-input").strip()
        for name, suggestions in COMMON_COSTING_SUGGESTIONS.items():
            field = self.fields.get(name)
            if not field:
                continue
            field.widget.attrs["list"] = f"costing-{name}-options"
        for name, help_text in HEADER_HELP_TEXTS.items():
            field = self.fields.get(name)
            if not field:
                continue
            field.help_text = help_text

    class Meta:
        model = CostingHeader
        fields = [
            "opportunity",
            "customer",
            "style_name",
            "style_code",
            "buyer",
            "brand",
            "product_type",
            "gender",
            "size_range",
            "season",
            "factory_location",
            "order_quantity",
            "moq",
            "costing_date",
            "merchandiser",
            "currency",
            "exchange_rate",
            "finance_percent_fabric",
            "finance_percent_trims",
            "commission_percent",
            "target_margin_percent",
            "manual_fob_per_piece",
            "fabric_type",
            "fabric_gsm",
            "fabric_composition",
            "wash_type",
            "print_type",
            "embroidery",
            "label_type",
            "packaging_type",
            "special_trims",
            "fit_remarks",
            "notes",
        ]
        widgets = {
            "style_name": forms.TextInput(attrs={"placeholder": "Style name"}),
            "style_code": forms.TextInput(attrs={"placeholder": "Style code"}),
            "buyer": forms.TextInput(attrs={"placeholder": "Buyer or division"}),
            "brand": forms.TextInput(attrs={"placeholder": "Brand / label"}),
            "gender": forms.TextInput(attrs={"placeholder": "Women, men, unisex, kids"}),
            "size_range": forms.TextInput(attrs={"placeholder": "XS-XL, 2-8Y, etc."}),
            "season": forms.TextInput(attrs={"placeholder": "Summer 2026, Holiday, core"}),
            "order_quantity": forms.NumberInput(attrs={"min": 0, "step": "1", "placeholder": "1000"}),
            "moq": forms.NumberInput(attrs={"min": 0, "step": "1", "placeholder": "MOQ"}),
            "costing_date": forms.DateInput(attrs={"type": "date"}),
            "merchandiser": forms.TextInput(attrs={"placeholder": "Merchandiser / owner"}),
            "exchange_rate": forms.NumberInput(attrs={"step": "0.01", "placeholder": "140.00"}),
            "finance_percent_fabric": forms.NumberInput(attrs={"step": "0.01", "placeholder": "2"}),
            "finance_percent_trims": forms.NumberInput(attrs={"step": "0.01", "placeholder": "2"}),
            "commission_percent": forms.NumberInput(attrs={"step": "0.01", "placeholder": "3"}),
            "target_margin_percent": forms.NumberInput(attrs={"step": "0.01", "placeholder": "35"}),
            "manual_fob_per_piece": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "fabric_type": forms.TextInput(attrs={"placeholder": "Single jersey, fleece, rib"}),
            "fabric_gsm": forms.TextInput(attrs={"placeholder": "220 GSM"}),
            "fabric_composition": forms.TextInput(attrs={"placeholder": "95% cotton / 5% spandex"}),
            "wash_type": forms.TextInput(attrs={"placeholder": "Garment wash, enzyme, none"}),
            "print_type": forms.TextInput(attrs={"placeholder": "Screen print, sublimation, none"}),
            "embroidery": forms.TextInput(attrs={"placeholder": "Embroidery details"}),
            "label_type": forms.TextInput(attrs={"placeholder": "Main label, care label, heat transfer"}),
            "packaging_type": forms.TextInput(attrs={"placeholder": "Polybag, carton, barcode, sticker"}),
            "special_trims": forms.TextInput(attrs={"placeholder": "Zipper, toggle, patch, cord, etc."}),
            "fit_remarks": forms.Textarea(attrs={"rows": 3, "placeholder": "Fit, construction, workmanship notes"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Assumptions and remarks"}),
        }
        labels = {
            "moq": "MOQ",
            "costing_date": "Costing date",
            "fabric_gsm": "Fabric GSM",
            "embroidery": "Embroidery detail",
            "fit_remarks": "Fit / construction remarks",
        }


class CostingSMVForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " costing-input").strip()
        self.fields["machine_smv"].help_text = "Sewing SMV for machine operations only."
        self.fields["finishing_smv"].help_text = "Extra SMV for finishing, pressing, packing, or handwork."
        self.fields["cpm"].help_text = "Cost per minute used by your factory for labor recovery."
        self.fields["efficiency_costing"].help_text = "Efficiency used in costing calculation, usually conservative."
        self.fields["efficiency_planned"].help_text = "Planned production efficiency for operational comparison."

    class Meta:
        model = CostingSMV
        fields = [
            "machine_smv",
            "finishing_smv",
            "cpm",
            "efficiency_costing",
            "efficiency_planned",
        ]
        widgets = {
            "machine_smv": forms.NumberInput(attrs={"step": "0.01", "placeholder": "10.5"}),
            "finishing_smv": forms.NumberInput(attrs={"step": "0.01", "placeholder": "2.5"}),
            "cpm": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.25"}),
            "efficiency_costing": forms.NumberInput(attrs={"step": "0.1", "placeholder": "70"}),
            "efficiency_planned": forms.NumberInput(attrs={"step": "0.1", "placeholder": "75"}),
        }


class CostSheetSimpleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "opportunity" in self.fields:
            self.fields["opportunity"].label_from_instance = _safe_opportunity_label

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
