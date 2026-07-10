from decimal import Decimal

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
    QuickCosting,
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
    "shipping_cost": "Order-level shipping cost. Leave blank to treat shipping as 0.",
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
    lead = getattr(opportunity, "lead", None)
    customer = getattr(opportunity, "customer", None)
    brand = (
        getattr(lead, "account_brand", "")
        or getattr(customer, "account_brand", "")
        or getattr(customer, "contact_name", "")
    )
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
            "shipping_cost",
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
            "shipping_cost": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
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
            "shipping_cost": "Shipping Cost",
        }

    def clean_shipping_cost(self):
        value = self.cleaned_data.get("shipping_cost")
        if value in (None, ""):
            return Decimal("0")
        if value < 0:
            raise forms.ValidationError("Enter a zero or positive amount.")
        return value


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


def is_local_sewing_initial(initial, instance):
    pricing_type = (initial or {}).get("pricing_type") or getattr(instance, "pricing_type", "")
    return pricing_type == QuickCosting.PRICING_CMT


class QuickCostingForm(forms.ModelForm):
    money_fields = [
        "material_cost",
        "production_cost",
        "other_expenses",
        "shipping_cost",
        "selling_price_per_piece",
        "commission_per_piece",
        "commission_value",
        "fabric_cost_per_kg",
        "fabric_consumption_kg_per_piece",
        "making_cost_per_piece",
        "print_embroidery_cost_per_piece",
        "trims_cost_per_piece",
        "packaging_cost_per_piece",
        "sewing_charge_per_piece_bdt",
        "sewing_cost_per_piece_bdt",
        "extra_local_cost_bdt",
    ]
    non_negative_messages = {
        "fabric_cost_per_kg": "Fabric cost per kg cannot be negative.",
        "fabric_consumption_kg_per_piece": "Fabric consumption cannot be negative.",
        "making_cost_per_piece": "Making cost per piece cannot be negative.",
        "print_embroidery_cost_per_piece": "Print or embroidery cost per piece cannot be negative.",
        "trims_cost_per_piece": "Trims cost per piece cannot be negative.",
        "packaging_cost_per_piece": "Packaging cost per piece cannot be negative.",
        "material_cost": "Legacy material cost cannot be negative.",
        "production_cost": "Legacy production cost cannot be negative.",
        "other_expenses": "Other expenses cannot be negative.",
        "shipping_cost": "Shipping total cannot be negative.",
        "selling_price_per_piece": "Selling price per piece cannot be negative.",
        "commission_per_piece": "Legacy commission per piece cannot be negative.",
        "commission_value": "Fixed commission cannot be less than 0.",
        "sewing_charge_per_piece_bdt": "Sewing charge cannot be negative.",
        "sewing_cost_per_piece_bdt": "Sewing cost cannot be negative.",
        "extra_local_cost_bdt": "Extra local cost cannot be negative.",
    }

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)
        opportunity = self.opportunity or getattr(self.instance, "opportunity", None)
        linked_salesperson = self._linked_salesperson(opportunity)
        self.fields["costing_purpose"].required = False
        self.fields["pricing_type"].required = False
        if not self.instance.pk and not self.initial.get("pricing_type"):
            self.initial["pricing_type"] = QuickCosting.PRICING_FULL_PACKAGE
        self.show_legacy_fields = bool(self.instance.pk and self.instance.currency is None)
        self.fields["currency"].required = not self.show_legacy_fields
        self.fields["currency"].error_messages["required"] = "Select BDT, CAD, or USD."
        self.fields["currency"].choices = [
            ("", "Select currency"),
            ("BDT", "BDT (৳)"),
            ("CAD", "CAD ($)"),
            ("USD", "USD ($)"),
        ]
        legacy_commission_posted = self.is_bound and (
            "commission_percent" in self.data or "commission_per_piece" in self.data
        )
        if not self.show_legacy_fields:
            for field_name in ("material_cost", "production_cost", "commission_per_piece", "commission_percent"):
                self.fields[field_name].widget = forms.HiddenInput()
                self.fields[field_name].disabled = not (
                    legacy_commission_posted and field_name in {"commission_per_piece", "commission_percent"}
                )
        for field_name in ("commission_type", "commission_value", "commission_currency"):
            self.fields[field_name].required = False
        self.fields["salesperson"].required = False
        self.fields["salesperson"].empty_label = "Not assigned"
        self.fields["salesperson"].queryset = self.fields["salesperson"].queryset.filter(
            is_active=True,
        ).order_by("first_name", "last_name", "username")
        if not self.is_bound and linked_salesperson and not getattr(self.instance, "salesperson_id", None):
            self.initial["salesperson"] = linked_salesperson.pk
        if not self.is_bound and is_local_sewing_initial(self.initial, self.instance):
            self.initial.setdefault("commission_currency", "BDT")
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " costing-input").strip()

    def _linked_salesperson(self, opportunity=None):
        opportunity = opportunity or self.opportunity or getattr(self.instance, "opportunity", None)
        if getattr(opportunity, "assigned_to", None):
            return opportunity.assigned_to
        lead = getattr(opportunity, "lead", None) if opportunity else None
        return getattr(lead, "assigned_to", None) if lead else None

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if not quantity or quantity < 1:
            raise forms.ValidationError("Enter an order quantity of at least 1 piece.")
        return quantity

    def clean_currency(self):
        currency = self.cleaned_data.get("currency")
        if not currency and not self.show_legacy_fields:
            raise forms.ValidationError("Select BDT, CAD, or USD.")
        return currency

    def clean_costing_purpose(self):
        return self.cleaned_data.get("costing_purpose") or QuickCosting.PURPOSE_BULK

    def clean_pricing_type(self):
        return self.cleaned_data.get("pricing_type") or QuickCosting.PRICING_FULL_PACKAGE

    def clean(self):
        cleaned = super().clean()
        is_local_sewing = cleaned.get("pricing_type") == QuickCosting.PRICING_CMT
        if is_local_sewing:
            cleaned["currency"] = "BDT"
            charge = cleaned.get("sewing_charge_per_piece_bdt")
            if charge is None or charge <= 0:
                self.add_error(
                    "sewing_charge_per_piece_bdt",
                    "Sewing charge per piece is required and must be greater than zero.",
                )
        else:
            cleaned["sewing_charge_per_piece_bdt"] = None
            cleaned["sewing_cost_per_piece_bdt"] = None
            cleaned["extra_local_cost_bdt"] = None
        for field_name in self.money_fields:
            value = cleaned.get(field_name)
            if field_name in {"shipping_cost", "commission_per_piece", "commission_value"} and value in (None, ""):
                cleaned[field_name] = Decimal("0")
                continue
            if value is not None and value < 0:
                self.add_error(field_name, self.non_negative_messages[field_name])
        exchange_rate = cleaned.get("exchange_rate_bdt_per_cad")
        if exchange_rate is not None and exchange_rate <= 0:
            self.add_error("exchange_rate_bdt_per_cad", "Exchange rate must be greater than zero when provided.")
        if (
            self.show_legacy_fields
            and self.instance.exchange_rate_bdt_per_cad is not None
            and exchange_rate is None
        ):
            self.add_error(
                "exchange_rate_bdt_per_cad",
                "Enter the BDT per CAD exchange rate to keep the existing CAD conversion.",
            )
        target_margin = cleaned.get("target_margin_percent")
        if target_margin is not None and target_margin < 0:
            self.add_error("target_margin_percent", "Enter a zero or positive margin.")
        commission_percent = cleaned.get("commission_percent")
        if commission_percent is not None and not Decimal("0") <= commission_percent <= Decimal("100"):
            self.add_error("commission_percent", "Commission must be between 0% and 100%.")
        commission_type = cleaned.get("commission_type") or QuickCosting.COMMISSION_NONE
        commission_value = cleaned.get("commission_value") or Decimal("0")
        commission_currency = cleaned.get("commission_currency") or cleaned.get("currency") or "BDT"
        cleaned["commission_type"] = commission_type
        cleaned["commission_value"] = commission_value
        cleaned["commission_currency"] = commission_currency
        if commission_type == QuickCosting.COMMISSION_NONE:
            cleaned["commission_value"] = Decimal("0")
            cleaned["commission_currency"] = "BDT"
        elif commission_value < 0:
            self.add_error("commission_value", "Fixed commission cannot be less than 0.")
        elif commission_type == QuickCosting.COMMISSION_PERCENTAGE and commission_value > Decimal("100"):
            self.add_error("commission_value", "Commission percentage must be between 0 and 100.")
        elif commission_type == QuickCosting.COMMISSION_FIXED:
            costing_currency = cleaned.get("currency") or "BDT"
            if commission_currency == "CAD" and costing_currency == "BDT" and not exchange_rate:
                self.add_error("exchange_rate_bdt_per_cad", "Enter an exchange rate for CAD commission.")
            elif commission_currency == "BDT" and costing_currency == "CAD" and not exchange_rate:
                self.add_error("exchange_rate_bdt_per_cad", "Enter an exchange rate for BDT commission.")
            elif "USD" in {commission_currency, costing_currency} and commission_currency != costing_currency:
                opportunity = self.opportunity or getattr(self.instance, "opportunity", None)
                usd_rate = getattr(opportunity, "fx_rate_bdt_per_usd", None) if opportunity else None
                if not usd_rate or usd_rate <= 0:
                    self.add_error("commission_currency", "USD commission conversion requires a linked opportunity with a USD to BDT rate.")
        fabric_cost = cleaned.get("fabric_cost_per_kg")
        fabric_consumption = cleaned.get("fabric_consumption_kg_per_piece")
        if (fabric_cost is None) != (fabric_consumption is None):
            message = "Enter both fabric cost per kg and fabric consumption per piece."
            self.add_error("fabric_cost_per_kg", message)
            self.add_error("fabric_consumption_kg_per_piece", message)
        if commission_type != QuickCosting.COMMISSION_NONE and not self.errors:
            projected = QuickCosting(
                buyer_name=cleaned.get("buyer_name") or "",
                project_name=cleaned.get("project_name") or "",
                product_type=cleaned.get("product_type") or "Other",
                costing_purpose=cleaned.get("costing_purpose") or QuickCosting.PURPOSE_BULK,
                pricing_type=cleaned.get("pricing_type") or QuickCosting.PRICING_FULL_PACKAGE,
                quantity=cleaned.get("quantity") or 0,
                currency=cleaned.get("currency") or "BDT",
                exchange_rate_bdt_per_cad=exchange_rate,
                fabric_cost_per_kg=cleaned.get("fabric_cost_per_kg"),
                fabric_consumption_kg_per_piece=cleaned.get("fabric_consumption_kg_per_piece"),
                making_cost_per_piece=cleaned.get("making_cost_per_piece"),
                print_embroidery_cost_per_piece=cleaned.get("print_embroidery_cost_per_piece"),
                trims_cost_per_piece=cleaned.get("trims_cost_per_piece"),
                packaging_cost_per_piece=cleaned.get("packaging_cost_per_piece"),
                material_cost=cleaned.get("material_cost") or Decimal("0"),
                production_cost=cleaned.get("production_cost") or Decimal("0"),
                other_expenses=cleaned.get("other_expenses") or Decimal("0"),
                shipping_cost=cleaned.get("shipping_cost") or Decimal("0"),
                selling_price_per_piece=cleaned.get("selling_price_per_piece") or Decimal("0"),
                sewing_charge_per_piece_bdt=cleaned.get("sewing_charge_per_piece_bdt"),
                sewing_cost_per_piece_bdt=cleaned.get("sewing_cost_per_piece_bdt"),
                extra_local_cost_bdt=cleaned.get("extra_local_cost_bdt"),
                commission_type=commission_type,
                commission_value=commission_value,
                commission_currency=commission_currency,
                target_margin_percent=target_margin,
                opportunity=self.opportunity or getattr(self.instance, "opportunity", None),
            )
            if projected.calculation_summary()["final_profit_after_commission"] < 0:
                confirmed = (self.data.get("confirm_negative_commission") or "").strip().lower() == "yes"
                if not confirmed:
                    raise forms.ValidationError(
                        "Sales commission makes net profit negative. Confirm the warning to save anyway."
                    )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.opportunity and not instance.opportunity_id:
            instance.opportunity = self.opportunity
        if not instance.salesperson_id:
            linked_salesperson = self._linked_salesperson(instance.opportunity)
            if linked_salesperson:
                instance.salesperson = linked_salesperson
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    class Meta:
        model = QuickCosting
        fields = [
            "buyer_name",
            "project_name",
            "product_type",
            "costing_purpose",
            "pricing_type",
            "quantity",
            "currency",
            "exchange_rate_bdt_per_cad",
            "fabric_cost_per_kg",
            "fabric_consumption_kg_per_piece",
            "making_cost_per_piece",
            "print_embroidery_cost_per_piece",
            "trims_cost_per_piece",
            "packaging_cost_per_piece",
            "material_cost",
            "production_cost",
            "other_expenses",
            "shipping_cost",
            "selling_price_per_piece",
            "commission_percent",
            "commission_per_piece",
            "salesperson",
            "commission_type",
            "commission_value",
            "commission_currency",
            "target_margin_percent",
            "sewing_charge_per_piece_bdt",
            "sewing_cost_per_piece_bdt",
            "extra_local_cost_bdt",
        ]
        widgets = {
            "buyer_name": forms.TextInput(attrs={"placeholder": "Buyer or company name"}),
            "project_name": forms.TextInput(attrs={"placeholder": "Project or style name"}),
            "costing_purpose": forms.Select(),
            "pricing_type": forms.Select(),
            "quantity": forms.NumberInput(attrs={"min": 1, "step": "1", "placeholder": "300"}),
            "currency": forms.Select(),
            "exchange_rate_bdt_per_cad": forms.NumberInput(attrs={"min": 0, "step": "0.0001", "placeholder": "90"}),
            "fabric_cost_per_kg": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "fabric_consumption_kg_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.0001", "placeholder": "0.0000"}),
            "making_cost_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "print_embroidery_cost_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "trims_cost_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "packaging_cost_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "material_cost": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "production_cost": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "other_expenses": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "shipping_cost": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "selling_price_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "commission_percent": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01", "placeholder": "5.00"}),
            "commission_per_piece": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "commission_value": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "target_margin_percent": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "30"}),
            "sewing_charge_per_piece_bdt": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "sewing_cost_per_piece_bdt": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
            "extra_local_cost_bdt": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "0.00"}),
        }
        labels = {
            "buyer_name": "Buyer Name",
            "project_name": "Project Name",
            "product_type": "Product Type",
            "costing_purpose": "Costing Purpose",
            "pricing_type": "Pricing Type",
            "currency": "Currency",
            "exchange_rate_bdt_per_cad": "Exchange Rate",
            "fabric_cost_per_kg": "Fabric Cost Per KG",
            "fabric_consumption_kg_per_piece": "Fabric Consumption KG Per Piece",
            "making_cost_per_piece": "Making Cost Per Piece",
            "print_embroidery_cost_per_piece": "Print or Embroidery Cost Per Piece",
            "trims_cost_per_piece": "Trims Cost Per Piece",
            "packaging_cost_per_piece": "Packaging Cost Per Piece",
            "material_cost": "Legacy Material Cost - Total Order",
            "production_cost": "Legacy Production Cost - Total Order",
            "other_expenses": "Other Expenses - Total Order",
            "shipping_cost": "Less Shipping Cost - Total Order",
            "selling_price_per_piece": "Selling Price Per Piece",
            "commission_percent": "Commission Percent",
            "commission_per_piece": "Legacy Commission Per Piece",
            "salesperson": "Salesperson",
            "commission_type": "Commission Type",
            "commission_value": "Commission Value",
            "commission_currency": "Commission Currency",
            "target_margin_percent": "Target Margin %",
            "sewing_charge_per_piece_bdt": "Sewing Charge Per Piece",
            "sewing_cost_per_piece_bdt": "Sewing Cost Per Piece",
            "extra_local_cost_bdt": "Extra Local Cost",
        }
        help_texts = {
            "currency": "Select BDT, CAD, or USD for this costing.",
            "pricing_type": "Choose Full Package, FOB, or CMT / Sewing Only.",
            "exchange_rate_bdt_per_cad": "Optional legacy conversion: BDT per 1 CAD.",
            "fabric_cost_per_kg": "Enter fabric price per KG.",
            "fabric_consumption_kg_per_piece": "Example: 0.42 KG per garment.",
            "making_cost_per_piece": "Cost basis: per piece in the selected currency.",
            "print_embroidery_cost_per_piece": "Cost basis: per piece in the selected currency.",
            "trims_cost_per_piece": "Cost basis: per piece in the selected currency.",
            "packaging_cost_per_piece": "Cost basis: per piece in the selected currency.",
            "material_cost": "Legacy BDT total order value. Used only when detailed per-piece costs are empty.",
            "production_cost": "Legacy BDT total order value. Used only when detailed per-piece costs are empty.",
            "other_expenses": "Cost basis: total order value in the selected currency.",
            "shipping_cost": "Internal shipping cost already included in selling price; deducted from profit only.",
            "selling_price_per_piece": "Cost basis: per piece in the selected currency.",
            "commission_percent": "Percentage of selling price.",
            "commission_per_piece": "Legacy absolute BDT amount per piece. Used only when commission percent is empty.",
            "salesperson": "Auto-filled from the linked opportunity or lead when available.",
            "commission_type": "Use None, Fixed Amount, or Percentage.",
            "commission_value": "Fixed amount or percentage value. Percentage is calculated from gross profit.",
            "commission_currency": "Keep CAD, USD, and BDT separate. Converted values appear only when exchange rates exist.",
            "target_margin_percent": "Percentage target for profit after commission.",
            "sewing_charge_per_piece_bdt": "Customer sewing charge per piece in BDT.",
            "sewing_cost_per_piece_bdt": "Internal sewing cost per piece in BDT. Leave blank when unavailable.",
            "extra_local_cost_bdt": "Additional local order cost in BDT.",
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
            "exchange_rate_bdt_per_cad": "Exchange rate (local currency per 1 CAD)",
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
