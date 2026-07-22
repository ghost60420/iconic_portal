# crm/forms.py
# Cleaned and fixed version of your full forms file
# Fixes:
# 1) Removed duplicate imports and duplicate class blocks
# 2) Kept only one MultiFileInput and one helper for multi upload
# 3) Fixed MultipleFileField to work with request.FILES.getlist
# 4) Fixed AccountingEntryForm attachments clean logic
# 5) Fixed BD daily form to lock BD rules on server and save subtype
# 6) Removed double "return cleaned"
# 7) Removed broken stray indented imports inside BDStaffMonthForm
# 8) Kept ShipmentForm helpers only once
# 9) Added safe money validation and stable widgets

from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import (
    AccountingEntry,
    AccountingAttachment,
    AccountingDocument,
    BDStaff,
    BDStaffMonth,
    Customer,
    Event,
    InventoryItem,
    Invoice,
    InvoicePayment,
    InvoiceSettings,
    Lead,
    Opportunity,
    ProductionOrder,
    Shipment,
    Product,
    ProductReferenceImage,
    Fabric,
    Accessory,
    Trim,
    ThreadOption,
    LibraryAttachment,
    lead_product_interest_choices,
)

# --------------------------------------------------
# Shared multi file widgets
# --------------------------------------------------
class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """
    Use this with MultiFileInput.
    In the view, use request.FILES.getlist("attachments") to save.
    This clean returns a list of files.
    """
    def clean(self, data, initial=None):
        if not data:
            return []
        parent_clean = super(MultipleFileField, self).clean
        if isinstance(data, (list, tuple)):
            return [parent_clean(f, initial) for f in data]
        return [parent_clean(data, initial)]


# --------------------------------------------------
# Lead form
# --------------------------------------------------
class LeadForm(forms.ModelForm):
    reference_image_1 = forms.ImageField(
        required=False,
        label="Image 1",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )
    reference_caption_1 = forms.CharField(
        required=False,
        label="Caption / style name 1",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Style 1 Hoodie"}),
    )
    reference_image_2 = forms.ImageField(
        required=False,
        label="Image 2",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )
    reference_caption_2 = forms.CharField(
        required=False,
        label="Caption / style name 2",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Style 2 T Shirt"}),
    )
    reference_image_3 = forms.ImageField(
        required=False,
        label="Image 3",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )
    reference_caption_3 = forms.CharField(
        required=False,
        label="Caption / style name 3",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Style 3 Sweatpant"}),
    )
    reference_image_4 = forms.ImageField(
        required=False,
        label="Image 4",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )
    reference_caption_4 = forms.CharField(
        required=False,
        label="Caption / style name 4",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Style 4"}),
    )
    reference_image_5 = forms.ImageField(
        required=False,
        label="Image 5",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )
    reference_caption_5 = forms.CharField(
        required=False,
        label="Caption / style name 5",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Style 5"}),
    )
    reference_image_6 = forms.ImageField(
        required=False,
        label="Image 6",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}),
    )
    reference_caption_6 = forms.CharField(
        required=False,
        label="Caption / style name 6",
        max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Style 6"}),
    )

    class Meta:
        model = Lead
        fields = [
            "account_brand",
            "website",
            "market",
            "country",
            "region",
            "city",
            "source",
            "source_channel",
            "lead_type",
            "brand_stage",
            "outbound_method",
            "outbound_status",
            "lead_status",
            "priority",
            "priority_level",
            "product_category",
            "primary_product_type",
            "product_interest",
            "order_quantity",
            "budget",
            "preferred_contact_time",
            "owner",
            "assigned_to",
            "next_follow_up_date",
            "last_outreach_date",
            "last_reply_date",
            "contact_name",
            "email",
            "phone",
            "instagram_handle",
            "linkedin_url",
            "attachment",
            "target_order_volume_min",
            "target_order_volume_max",
            "brand_fit_score",
            "ideal_customer_profile_match",
            "qualification_status",
            "qualification_reason",
            "confidence_level",
            "target_order_range_estimate",
            "product_category_guess",
            "recommended_channel",
            "recommended_next_action",
            "disqualification_reason",
            "notes",
        ]
        widgets = {
            "next_follow_up_date": forms.DateInput(attrs={"type": "date"}),
            "last_outreach_date": forms.DateInput(attrs={"type": "date"}),
            "last_reply_date": forms.DateInput(attrs={"type": "date"}),
            "target_order_volume_min": forms.NumberInput(attrs={"min": 0, "step": 1}),
            "target_order_volume_max": forms.NumberInput(attrs={"min": 0, "step": 1}),
            "brand_fit_score": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 1}),
            "website": forms.TextInput(attrs={"placeholder": "https://"}),
            "instagram_handle": forms.TextInput(attrs={"placeholder": "@brand"}),
            "linkedin_url": forms.URLInput(attrs={"placeholder": "https://linkedin.com/company/..."}),
            "qualification_reason": forms.Textarea(attrs={"rows": 3}),
            "recommended_channel": forms.TextInput(attrs={"placeholder": "Email, Instagram"}),
            "recommended_next_action": forms.TextInput(attrs={"placeholder": "Follow up, call, etc"}),
            "target_order_range_estimate": forms.TextInput(attrs={"placeholder": "1000-5000 pcs"}),
            "product_category_guess": forms.TextInput(attrs={"placeholder": "Activewear"}),
            "confidence_level": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        category_choices = [("", "Select a category")] + list(Opportunity.PRODUCT_CATEGORY_CHOICES)
        interest_choices = [("", "Select an interest")] + list(lead_product_interest_choices())

        if "product_category" in self.fields:
            self.fields["product_category"].choices = category_choices
            self.fields["product_category"].required = True
            self.fields["product_category"].widget.attrs.update({"class": "form-control"})

            current_value = getattr(self.instance, "product_category", "") or ""
            if current_value and current_value not in dict(Opportunity.PRODUCT_CATEGORY_CHOICES):
                self.fields["product_category"].choices.append((current_value, current_value))

        if "primary_product_type" in self.fields:
            self.fields["primary_product_type"].choices = [("", "Select a product type")] + list(Opportunity.PRODUCT_TYPE_CHOICES)
            self.fields["primary_product_type"].required = False
            self.fields["primary_product_type"].widget.attrs.update({"class": "form-control"})

        if "product_interest" in self.fields:
            self.fields["product_interest"].choices = interest_choices
            self.fields["product_interest"].required = True
            self.fields["product_interest"].widget.attrs.update({"class": "form-control"})

            current_value = getattr(self.instance, "product_interest", "") or ""
            if current_value and current_value not in dict(self.fields["product_interest"].choices):
                self.fields["product_interest"].choices.append((current_value, current_value))

        if "assigned_to" in self.fields:
            self.fields["assigned_to"].queryset = get_user_model().objects.filter(
                is_active=True,
                employee_profile__is_archived=False,
            ).order_by("first_name", "last_name", "username")
            self.fields["assigned_to"].required = False
            self.fields["assigned_to"].empty_label = "Unassigned"
            self.fields["assigned_to"].widget.attrs.update({"class": "form-control"})

        if not self.instance.pk and not self.is_bound and "lead_type" in self.fields:
            self.fields["lead_type"].initial = "outbound"

    def _clean_reference_image(self, field_name):
        image = self.cleaned_data.get(field_name)
        if not image:
            return image
        extension = "." + image.name.rsplit(".", 1)[-1].lower() if "." in image.name else ""
        if extension not in ProductReferenceImage.ALLOWED_EXTENSIONS:
            raise ValidationError("Upload a JPG, PNG, or WEBP image.")
        if getattr(image, "size", 0) and image.size > ProductReferenceImage.MAX_UPLOAD_SIZE_BYTES:
            raise ValidationError("Reference image file size must be 8MB or smaller.")
        return image

    def clean_reference_image_1(self):
        return self._clean_reference_image("reference_image_1")

    def clean_reference_image_2(self):
        return self._clean_reference_image("reference_image_2")

    def clean_reference_image_3(self):
        return self._clean_reference_image("reference_image_3")

    def clean_reference_image_4(self):
        return self._clean_reference_image("reference_image_4")

    def clean_reference_image_5(self):
        return self._clean_reference_image("reference_image_5")

    def clean_reference_image_6(self):
        return self._clean_reference_image("reference_image_6")


# --------------------------------------------------
# Quick outbound lead form
# --------------------------------------------------
class QuickOutboundLeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = [
            "account_brand",
            "contact_name",
            "email",
            "phone",
            "website",
            "instagram_handle",
            "linkedin_url",
            "country",
            "product_interest",
            "target_order_volume_min",
            "target_order_volume_max",
            "source_channel",
            "outbound_method",
            "assigned_to",
            "notes",
        ]
        widgets = {
            "website": forms.TextInput(attrs={"placeholder": "https://"}),
            "instagram_handle": forms.TextInput(attrs={"placeholder": "@brand"}),
            "linkedin_url": forms.URLInput(attrs={"placeholder": "https://linkedin.com/company/..."}),
            "target_order_volume_min": forms.NumberInput(attrs={"min": 0, "step": 1}),
            "target_order_volume_max": forms.NumberInput(attrs={"min": 0, "step": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "product_interest" in self.fields:
            self.fields["product_interest"].choices = [("", "Select an interest")] + list(lead_product_interest_choices())
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].queryset = get_user_model().objects.filter(
                is_active=True,
                employee_profile__is_archived=False,
            ).order_by("first_name", "last_name", "username")

# --------------------------------------------------
# Library forms
# --------------------------------------------------
class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "product_code",
            "name",
            "product_type",
            "product_category",
            "default_gsm",
            "default_fabric",
            "default_moq",
            "default_price",
            "image",
            "notes",
            "is_active",
        ]


class FabricForm(forms.ModelForm):
    class Meta:
        model = Fabric
        fields = [
            "fabric_code",
            "name",
            "fabric_group",
            "fabric_type",
            "weave",
            "knit_structure",
            "construction",
            "composition",
            "gsm",
            "stretch_type",
            "surface",
            "handfeel",
            "drape",
            "warmth",
            "weight_class",
            "breathability",
            "sheerness",
            "shrinkage",
            "durability",
            "color_options",
            "price_per_kg",
            "price_per_meter",
            "image",
            "notes",
            "is_active",
        ]


class AccessoryForm(forms.ModelForm):
    class Meta:
        model = Accessory
        fields = [
            "accessory_code",
            "name",
            "accessory_type",
            "size",
            "color",
            "material",
            "finish",
            "supplier",
            "price_per_unit",
            "image",
            "notes",
            "is_active",
        ]


class TrimForm(forms.ModelForm):
    class Meta:
        model = Trim
        fields = [
            "trim_code",
            "name",
            "trim_type",
            "width",
            "color",
            "material",
            "price_per_meter",
            "image",
            "notes",
            "is_active",
        ]


class ThreadForm(forms.ModelForm):
    class Meta:
        model = ThreadOption
        fields = [
            "thread_code",
            "name",
            "thread_type",
            "count",
            "color",
            "brand",
            "use_for",
            "price_per_cone",
            "image",
            "notes",
            "is_active",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def clean_email(self):
        v = (self.cleaned_data.get("email") or "").strip().lower()
        if v and "@" not in v:
            raise forms.ValidationError("Email is not valid.")
        return v


# --------------------------------------------------


class LibraryAttachmentForm(forms.ModelForm):
    class Meta:
        model = LibraryAttachment
        fields = ["title", "category", "file", "note"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Catalog or document title"}),
            "note": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note"}),
        }
# Accounting entry form (supports multi attachments)
# --------------------------------------------------
STATUS_CHOICES = [
    ("", "Select"),
    ("PAID", "Paid"),
    ("UNPAID", "Unpaid"),
    ("PENDING", "Pending"),
    ("PARTIAL", "Partial"),
    ("CANCELLED", "Cancelled"),
]

MAIN_TYPE_CHOICES = [
    ("", "Select"),
    ("INCOME", "Income"),
    ("COGS", "COGS"),
    ("EXPENSE", "Expense"),
    ("TRANSFER", "Transfer"),
    ("TAX", "Tax"),
    ("OTHER", "Other"),
]

BD_MAIN_TYPE_CHOICES = [
    ("", "Select"),
    ("Office Rent", "Office Rent"),
    ("Utility Bill", "Utility Bill"),
    ("WiFi Bill", "WiFi Bill"),
    ("Salary", "Salary"),
    ("Overtime", "Overtime"),
    ("Transport", "Transport"),
    ("Food", "Food"),
    ("Production Cost", "Production Cost"),
    ("Sewing Cost", "Sewing Cost"),
    ("Fabric", "Fabric"),
    ("Accessories", "Accessories"),
    ("Printing", "Printing"),
    ("Embroidery", "Embroidery"),
    ("Delivery", "Delivery"),
    ("Maintenance", "Maintenance"),
    ("Loan", "Loan"),
    ("Advance", "Advance"),
    ("Customer Payment", "Customer Payment"),
    ("Other Income", "Other Income"),
    ("Other Expense", "Other Expense"),
]

BD_FLOW_CHOICES = [
    ("IN", "IN"),
    ("OUT", "OUT"),
]


def _choices_with_current(choices, *values):
    normalized = {str(value) for value, _label in choices}
    result = list(choices)
    for value in values:
        value = (value or "").strip()
        if value and value not in normalized:
            result.append((value, value))
            normalized.add(value)
    return result


class AccountingEntryForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        widget=MultiFileInput(attrs={"class": "form-control", "multiple": True}),
    )

    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    main_type = forms.ChoiceField(
        choices=MAIN_TYPE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = AccountingEntry
        fields = [
            "date",
            "side",
            "direction",
            "status",
            "main_type",
            "sub_type",
            "customer",
            "opportunity",
            "production_order",
            "shipment",
            "currency",
            "amount_original",
            "rate_to_cad",
            "rate_to_bdt",
            "description",
            "internal_note",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "side": forms.Select(attrs={"class": "form-select"}),
            "direction": forms.Select(attrs={"class": "form-select"}),
            "sub_type": forms.TextInput(attrs={"class": "form-control"}),
            "customer": forms.Select(attrs={"class": "form-select"}),
            "opportunity": forms.Select(attrs={"class": "form-select"}),
            "production_order": forms.Select(attrs={"class": "form-select"}),
            "shipment": forms.Select(attrs={"class": "form-select"}),
            "currency": forms.Select(attrs={"class": "form-select"}),
            "amount_original": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "rate_to_cad": forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
            "rate_to_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "internal_note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, lock_side=None, lock_direction=None, lock_currency=None, bd_mode=False, **kwargs):
        super().__init__(*args, **kwargs)

        current_status = ""
        current_main_type = ""
        if self.instance and self.instance.pk:
            current_status = (self.instance.status or "").strip()
            current_main_type = (self.instance.main_type or "").strip()
        current_status = current_status or (self.initial.get("status") or "")
        current_main_type = current_main_type or (self.initial.get("main_type") or "")
        legacy_main_type = ""
        if self.is_bound:
            posted_main_type = (self.data.get(self.add_prefix("main_type")) or "").strip()
            legacy_values = {value for value, _label in MAIN_TYPE_CHOICES if value}
            if posted_main_type in legacy_values:
                legacy_main_type = posted_main_type

        if bd_mode:
            if "direction" in self.fields:
                self.fields["direction"].choices = BD_FLOW_CHOICES
            if "status" in self.fields:
                self.fields["status"].choices = _choices_with_current(STATUS_CHOICES, current_status)
            if "main_type" in self.fields:
                self.fields["main_type"].choices = _choices_with_current(
                    BD_MAIN_TYPE_CHOICES,
                    current_main_type,
                    legacy_main_type,
                )

        if self.instance and self.instance.pk:
            if "status" in self.fields:
                self.fields["status"].initial = current_status
            if "main_type" in self.fields:
                self.fields["main_type"].initial = current_main_type

        if lock_side and "side" in self.fields:
            self.fields["side"].initial = lock_side
            self.fields["side"].disabled = True

        if lock_direction and "direction" in self.fields:
            self.fields["direction"].initial = lock_direction
            self.fields["direction"].disabled = True

        if lock_currency and "currency" in self.fields:
            self.fields["currency"].initial = lock_currency
            self.fields["currency"].disabled = True

        for name in ("side", "direction", "currency"):
            if name in self.fields and getattr(self.fields[name], "disabled", False):
                css = self.fields[name].widget.attrs.get("class", "")
                self.fields[name].widget.attrs["class"] = (css + " opacity-75").strip()

    def clean(self):
        cleaned = super().clean()

        if "side" in self.fields and self.fields["side"].disabled:
            cleaned["side"] = self.fields["side"].initial

        if "direction" in self.fields and self.fields["direction"].disabled:
            cleaned["direction"] = self.fields["direction"].initial

        if "currency" in self.fields and self.fields["currency"].disabled:
            cleaned["currency"] = self.fields["currency"].initial

        currency = (cleaned.get("currency") or "").upper().strip()
        rate_to_cad = cleaned.get("rate_to_cad")
        rate_to_bdt = cleaned.get("rate_to_bdt")

        if currency == "CAD":
            cleaned["rate_to_cad"] = Decimal("1")
            if not rate_to_bdt or rate_to_bdt <= 0:
                cad_to_bdt = _latest_rate_bdt_per_cad()
                if cad_to_bdt and cad_to_bdt > 0:
                    cleaned["rate_to_bdt"] = cad_to_bdt
        elif currency == "BDT":
            cleaned["rate_to_bdt"] = Decimal("1")
            if not rate_to_cad or rate_to_cad <= 0:
                cad_to_bdt = _latest_rate_bdt_per_cad()
                if cad_to_bdt and cad_to_bdt > 0:
                    cleaned["rate_to_cad"] = cad_to_bdt

        return cleaned

    def clean_attachments(self):
        files = self.files.getlist("attachments")
        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed per entry.")
        return files


# --------------------------------------------------
# Accounting document forms
# --------------------------------------------------
class AccountingDocumentForm(forms.ModelForm):
    class Meta:
        model = AccountingDocument
        fields = [
            "side",
            "doc_type",
            "title",
            "vendor",
            "amount",
            "doc_date",
            "file",
            "note",
            "linked_entry",
        ]
        widgets = {
            "doc_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "note": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, lock_side=None, **kwargs):
        super().__init__(*args, **kwargs)
        if lock_side and "side" in self.fields:
            self.fields["side"].initial = lock_side
            self.fields["side"].disabled = True

    def clean(self):
        cleaned = super().clean()
        if "side" in self.fields and self.fields["side"].disabled:
            cleaned["side"] = self.fields["side"].initial
        return cleaned


class AccountingDocsUploadForm(forms.Form):
    files = forms.FileField(
        required=True,
        widget=MultiFileInput(attrs={"multiple": True, "class": "form-control"}),
        help_text="Upload one or more files",
    )

    direction = forms.ChoiceField(
        required=True,
        choices=[("IN", "IN"), ("OUT", "OUT")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    main_type = forms.ChoiceField(
        required=True,
        choices=[
            ("INCOME", "INCOME"),
            ("COGS", "COGS"),
            ("EXPENSE", "EXPENSE"),
            ("TRANSFER", "TRANSFER"),
            ("TAX", "TAX"),
            ("OTHER", "OTHER"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    sub_type = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Utilities, Rent, etc"}),
    )

    amount = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=14,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "0.00"}),
    )

    invoice_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Invoice number"}),
    )

    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "What is this for"}),
    )

    def clean_files(self):
        files = self.files.getlist("files")
        if not files:
            raise forms.ValidationError("Please upload at least one file.")
        if len(files) > 20:
            raise forms.ValidationError("Maximum 20 files allowed.")
        return files


class AccountingEntryAttachForm(forms.Form):
    files = forms.FileField(
        required=True,
        widget=MultiFileInput(attrs={"multiple": True, "class": "form-control"}),
        help_text="You can select more than one file.",
    )

    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_files(self):
        files = self.files.getlist("files")
        if not files:
            raise forms.ValidationError("Please upload at least one file.")
        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed.")
        return files


# --------------------------------------------------
# Shipment form helpers and form
# --------------------------------------------------
def _shipment_order_field_name():
    names = {f.name for f in Shipment._meta.fields}
    if "production_order" in names:
        return "production_order"
    if "order" in names:
        return "order"
    return None


def _model_field_names(model):
    return {f.name for f in model._meta.fields}


def _latest_rate_bdt_per_cad():
    try:
        from .models import ExchangeRate
        row = ExchangeRate.objects.order_by("-updated_at").first()
        if row and row.cad_to_bdt:
            return Decimal(str(row.cad_to_bdt))
    except Exception:
        return None
    return None


ORDER_FIELD = _shipment_order_field_name()
SHIPMENT_FIELDS = _model_field_names(Shipment)


class ShipmentForm(forms.ModelForm):
    rate_bdt_per_cad = forms.DecimalField(required=False, max_digits=14, decimal_places=4)

    class Meta:
        model = Shipment
        fields = [
            "opportunity",
            "customer",
            "carrier",
            "tracking_number",
            "ship_date",
            "shipment_type",
            "status",
            "box_count",
            "total_weight_kg",
            "cost_bdt",
            "cost_cad",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        can_edit_internal_costing = kwargs.pop("can_edit_internal_costing", False)
        super().__init__(*args, **kwargs)

        if not can_edit_internal_costing:
            for field_name in ("cost_bdt", "cost_cad", "rate_bdt_per_cad"):
                self.fields.pop(field_name, None)

        if ORDER_FIELD and ORDER_FIELD in SHIPMENT_FIELDS and ORDER_FIELD not in self.fields:
            fk_model = Shipment._meta.get_field(ORDER_FIELD).remote_field.model
            self.fields[ORDER_FIELD] = forms.ModelChoiceField(
                queryset=fk_model.objects.all(),
                required=False,
            )
            ordered = [ORDER_FIELD] + [k for k in self.fields.keys() if k != ORDER_FIELD]
            self.order_fields(ordered)

        for field_name, placeholder in {
            "opportunity": "Search opportunity ID or brand",
            "customer": "Search customer or contact",
            ORDER_FIELD: "Search production order or style",
        }.items():
            if field_name and field_name in self.fields:
                css = self.fields[field_name].widget.attrs.get("class", "form-select")
                self.fields[field_name].widget.attrs["class"] = f"{css} crm-searchable-select".strip()
                self.fields[field_name].widget.attrs["data-search-placeholder"] = placeholder

        for f in ["box_count", "total_weight_kg", "cost_bdt", "cost_cad"]:
            if f in self.fields:
                self.fields[f].required = False

        if not self.initial.get("rate_bdt_per_cad"):
            r = _latest_rate_bdt_per_cad()
            if r is not None:
                self.initial["rate_bdt_per_cad"] = r

    def clean(self):
        cleaned = super().clean()

        rate = cleaned.get("rate_bdt_per_cad")
        cost_bdt = cleaned.get("cost_bdt")
        cost_cad = cleaned.get("cost_cad")
        try:
            if rate is not None and Decimal(str(rate)) <= 0:
                cleaned["rate_bdt_per_cad"] = None
        except Exception:
            cleaned["rate_bdt_per_cad"] = None

        if (rate in [None, ""]) and cost_bdt and cost_cad and Decimal(str(cost_cad)) != 0:
            try:
                cleaned["rate_bdt_per_cad"] = (Decimal(str(cost_bdt)) / Decimal(str(cost_cad))).quantize(
                    Decimal("0.0001")
                )
            except Exception:
                pass

        if cleaned.get("rate_bdt_per_cad") in [None, ""]:
            r = _latest_rate_bdt_per_cad()
            if r is not None:
                cleaned["rate_bdt_per_cad"] = r

        return cleaned


# --------------------------------------------------
# Inventory form
# --------------------------------------------------
class InventoryItemForm(forms.ModelForm):
    INTERNAL_FIELDS = {
        "unit_cost",
        "supplier_name",
        "incoming_quantity",
        "reserved_quantity",
        "damaged_quantity",
        "waste_quantity",
    }

    class Meta:
        model = InventoryItem
        fields = "__all__"
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, can_edit_internal_costing=True, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-control")
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control")
        if not can_edit_internal_costing:
            for field_name in self.INTERNAL_FIELDS:
                self.fields.pop(field_name, None)


# --------------------------------------------------
# Event form
# --------------------------------------------------
class EventForm(forms.ModelForm):
    reminder_minutes_before = forms.TypedChoiceField(
        required=False,
        coerce=int,
        empty_value=None,
        choices=[
            ("", "No CRM reminder"),
            (15, "15 minutes before"),
            (60, "1 hour before"),
            (1440, "1 day before"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Event
        fields = [
            "title",
            "start_datetime",
            "end_datetime",
            "event_type",
            "priority",
            "status",
            "location",
            "meeting_link",
            "lead",
            "opportunity",
            "customer",
            "production",
            "production_stage",
            "assigned_to_name",
            "assigned_to_email",
            "attendees",
            "external_attendees",
            "reminder_minutes_before",
            "note",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "start_datetime": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
            "end_datetime": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
            "event_type": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "location": forms.TextInput(attrs={"class": "form-control", "placeholder": "Office, showroom, Zoom, Google Meet"}),
            "meeting_link": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://..."}),
            "lead": forms.Select(attrs={"class": "form-select crm-searchable-select", "data-search-placeholder": "Search lead ID, brand, or contact"}),
            "opportunity": forms.Select(attrs={"class": "form-select crm-searchable-select", "data-search-placeholder": "Search opportunity ID or brand"}),
            "customer": forms.Select(attrs={"class": "form-select crm-searchable-select", "data-search-placeholder": "Search customer or contact"}),
            "production": forms.Select(attrs={"class": "form-select crm-searchable-select", "data-search-placeholder": "Search production order or style"}),
            "production_stage": forms.Select(attrs={"class": "form-select"}),
            "assigned_to_name": forms.TextInput(attrs={"class": "form-control"}),
            "assigned_to_email": forms.EmailInput(attrs={"class": "form-control"}),
            "attendees": forms.SelectMultiple(attrs={"class": "form-select", "size": 6}),
            "external_attendees": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "client@example.com, buyer@example.com"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        optional_fields = [
            "end_datetime",
            "note",
            "lead",
            "opportunity",
            "customer",
            "production",
            "production_stage",
            "assigned_to_name",
            "assigned_to_email",
            "attendees",
            "external_attendees",
            "reminder_minutes_before",
            "location",
            "meeting_link",
        ]
        for k in optional_fields:
            if k in self.fields:
                self.fields[k].required = False

        if "lead" in self.fields:
            self.fields["lead"].queryset = Lead.objects.order_by("-id")
        if "opportunity" in self.fields:
            self.fields["opportunity"].queryset = Opportunity.objects.order_by("-id")
        if "customer" in self.fields:
            self.fields["customer"].queryset = Customer.objects.order_by("id")
        if "production" in self.fields:
            self.fields["production"].queryset = ProductionOrder.objects.select_related("customer", "opportunity").order_by("-created_at", "-id")
            self.fields["production"].label = "Production order"
        if "attendees" in self.fields:
            self.fields["attendees"].queryset = get_user_model().objects.filter(is_active=True).order_by("first_name", "last_name", "username")
            self.fields["attendees"].label = "Internal attendees"
            self.fields["attendees"].help_text = "CRM users who should see this meeting on their calendar."
        if "external_attendees" in self.fields:
            self.fields["external_attendees"].label = "External attendee emails"
        if "reminder_minutes_before" in self.fields:
            current_minutes = getattr(self.instance, "reminder_minutes_before", None)
            preset_values = {"", 15, 60, 1440}
            if current_minutes not in preset_values and current_minutes is not None:
                self.fields["reminder_minutes_before"].choices = [
                    *self.fields["reminder_minutes_before"].choices,
                    (current_minutes, f"{current_minutes} minutes before"),
                ]


# --------------------------------------------------
# BD staff forms
# --------------------------------------------------
class BDStaffForm(forms.ModelForm):
    class Meta:
        model = BDStaff
        fields = ["name", "role", "base_salary_bdt", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Staff name"}),
            "role": forms.TextInput(attrs={"class": "form-control", "placeholder": "Role"}),
            "base_salary_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class BDStaffMonthForm(forms.ModelForm):
    class Meta:
        model = BDStaffMonth
        fields = [
            "base_salary_bdt",
            "overtime_hours",
            "overtime_rate_bdt",
            "bonus_bdt",
            "deduction_bdt",
            "is_paid",
            "paid_date",
            "note",
        ]
        widgets = {
            "base_salary_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "overtime_hours": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "overtime_rate_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "bonus_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "deduction_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "is_paid": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "paid_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "note": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "paid_date" in self.fields:
            self.fields["paid_date"].required = False

    def clean(self):
        cleaned = super().clean()

        is_paid = cleaned.get("is_paid")
        paid_date = cleaned.get("paid_date")

        if is_paid and not paid_date:
            cleaned["paid_date"] = timezone.localdate()

        if not is_paid:
            cleaned["paid_date"] = None

        return cleaned


# --------------------------------------------------
# Invoice form
# --------------------------------------------------
class InvoiceForm(forms.ModelForm):
    historical_entry_mode = forms.BooleanField(
        required=False,
        label="Historical Entry",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = Invoice
        fields = [
            "order",
            "customer",
            "invoice_number",
            "issue_date",
            "invoice_date",
            "due_date",
            "currency",
            "invoice_market",
            "invoice_type",
            "deposit_percentage",
            "subtotal",
            "shipping_amount",
            "discount_amount",
            "tax_amount",
            "total_amount",
            "paid_amount",
            "status",
            "notes",
            "sewing_charge",
            "other_internal_cost",
            "internal_cost_note",
        ]
        widgets = {
            "invoice_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto if blank"}),
            "issue_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "invoice_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "invoice_market": forms.Select(attrs={"class": "form-select"}),
            "invoice_type": forms.Select(attrs={"class": "form-select"}),
            "deposit_percentage": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
            "subtotal": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "shipping_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "discount_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "tax_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "readonly": "readonly"}),
            "paid_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "sewing_charge": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "other_internal_cost": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "internal_cost_note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        can_edit_internal_costs = kwargs.pop("can_edit_internal_costs", False)
        can_edit_historical_dates = kwargs.pop("can_edit_historical_dates", False)
        super().__init__(*args, **kwargs)

        if "invoice_number" in self.fields:
            self.fields["invoice_number"].required = False

        if "total_amount" in self.fields:
            self.fields["total_amount"].required = False
            self.fields["total_amount"].disabled = True

        if "status" in self.fields:
            self.fields["status"].required = False

        if can_edit_historical_dates:
            if "historical_entry_mode" in self.fields:
                self.fields["historical_entry_mode"].required = False
                self.fields["historical_entry_mode"].initial = bool(getattr(self.instance, "invoice_date", None))
            if "invoice_date" in self.fields:
                self.fields["invoice_date"].required = False
        else:
            self.fields.pop("historical_entry_mode", None)
            self.fields.pop("invoice_date", None)

        for field_name in ("invoice_market", "invoice_type"):
            if field_name in self.fields:
                self.fields[field_name].required = False

        if "deposit_percentage" in self.fields:
            self.fields["deposit_percentage"].required = False

        if "invoice_type" in self.fields and not can_edit_internal_costs:
            choices = [choice for choice in self.fields["invoice_type"].choices if choice[0] != "sewing_charge"]
            if getattr(self.instance, "invoice_type", "") == "sewing_charge":
                choices.append(("sewing_charge", "Client charge invoice"))
            self.fields["invoice_type"].choices = choices

        if can_edit_internal_costs:
            for field_name in ("sewing_charge", "other_internal_cost", "internal_cost_note"):
                if field_name in self.fields:
                    self.fields[field_name].required = False
        else:
            for field_name in ("sewing_charge", "other_internal_cost", "internal_cost_note"):
                self.fields.pop(field_name, None)

    def _clean_money(self, field_name):
        v = self.cleaned_data.get(field_name)
        if v in ("", None):
            return Decimal("0")
        try:
            return Decimal(str(v))
        except Exception:
            return Decimal("0")

    def clean_subtotal(self):
        return self._clean_money("subtotal")

    def clean_shipping_amount(self):
        return self._clean_money("shipping_amount")

    def clean_discount_amount(self):
        return self._clean_money("discount_amount")

    def clean_tax_amount(self):
        return self._clean_money("tax_amount")

    def clean_invoice_market(self):
        return self.cleaned_data.get("invoice_market") or "north_america"

    def clean_invoice_type(self):
        return self.cleaned_data.get("invoice_type") or "bulk"

    def clean_deposit_percentage(self):
        value = self.cleaned_data.get("deposit_percentage")
        if value in ("", None):
            invoice_type = (self.cleaned_data.get("invoice_type") or "").strip()
            invoice_market = (self.cleaned_data.get("invoice_market") or "").strip()
            settings_obj = InvoiceSettings.active()
            if invoice_type == "sample":
                return getattr(settings_obj, "default_sample_deposit_percentage", None) or Decimal("100")
            if invoice_market == "bangladesh" and invoice_type == "sewing_charge":
                return getattr(settings_obj, "default_bd_sewing_deposit_percentage", None) or Decimal("30")
            return getattr(settings_obj, "default_bulk_deposit_percentage", None) or Decimal("30")
        try:
            value = Decimal(str(value))
        except Exception:
            return Decimal("50")
        if value < 0:
            return Decimal("0")
        if value > 100:
            return Decimal("100")
        return value

    def clean(self):
        cleaned = super().clean()
        if "historical_entry_mode" not in self.fields:
            return cleaned

        historical_mode = cleaned.get("historical_entry_mode")
        invoice_date = cleaned.get("invoice_date")
        if historical_mode and not invoice_date:
            self.add_error("invoice_date", "Invoice Date is required when Historical Entry mode is enabled.")
        if not historical_mode:
            cleaned["invoice_date"] = None
        return cleaned

    def clean_paid_amount(self):
        return self._clean_money("paid_amount")

    def clean_sewing_charge(self):
        return self._clean_money("sewing_charge")

    def clean_other_internal_cost(self):
        return self._clean_money("other_internal_cost")


class InvoiceSettingsForm(forms.ModelForm):
    class Meta:
        model = InvoiceSettings
        fields = [
            "company_name",
            "company_email",
            "company_phone",
            "website",
            "slogan",
            "invoice_footer_note",
            "authorized_by_name",
            "authorized_by_title",
            "paypal_email_or_id",
            "paypal_qr_image",
            "etransfer_email",
            "canada_bank_name",
            "canada_account_name",
            "canada_account_number",
            "canada_transit_number",
            "canada_institution_number",
            "canada_wire_note",
            "canada_payment_terms",
            "bd_bank_name",
            "bd_account_name",
            "bd_account_number",
            "bd_branch",
            "bd_routing_number",
            "bd_swift",
            "bkash_number",
            "bkash_qr_image",
            "nagad_number",
            "nagad_qr_image",
            "rocket_number",
            "rocket_qr_image",
            "bd_payment_terms",
            "default_sample_deposit_percentage",
            "default_bulk_deposit_percentage",
            "default_bd_sewing_deposit_percentage",
            "default_currency_na",
            "default_currency_bd",
            "default_tax_note",
            "terms_and_conditions_na",
            "terms_and_conditions_bd",
            "is_active",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"class": "form-control"}),
            "company_email": forms.EmailInput(attrs={"class": "form-control"}),
            "company_phone": forms.TextInput(attrs={"class": "form-control"}),
            "website": forms.TextInput(attrs={"class": "form-control"}),
            "slogan": forms.TextInput(attrs={"class": "form-control"}),
            "invoice_footer_note": forms.TextInput(attrs={"class": "form-control"}),
            "authorized_by_name": forms.TextInput(attrs={"class": "form-control"}),
            "authorized_by_title": forms.TextInput(attrs={"class": "form-control"}),
            "paypal_email_or_id": forms.TextInput(attrs={"class": "form-control"}),
            "paypal_qr_image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "etransfer_email": forms.EmailInput(attrs={"class": "form-control"}),
            "canada_bank_name": forms.TextInput(attrs={"class": "form-control"}),
            "canada_account_name": forms.TextInput(attrs={"class": "form-control"}),
            "canada_account_number": forms.TextInput(attrs={"class": "form-control"}),
            "canada_transit_number": forms.TextInput(attrs={"class": "form-control"}),
            "canada_institution_number": forms.TextInput(attrs={"class": "form-control"}),
            "canada_wire_note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "canada_payment_terms": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "bd_bank_name": forms.TextInput(attrs={"class": "form-control"}),
            "bd_account_name": forms.TextInput(attrs={"class": "form-control"}),
            "bd_account_number": forms.TextInput(attrs={"class": "form-control"}),
            "bd_branch": forms.TextInput(attrs={"class": "form-control"}),
            "bd_routing_number": forms.TextInput(attrs={"class": "form-control"}),
            "bd_swift": forms.TextInput(attrs={"class": "form-control"}),
            "bkash_number": forms.TextInput(attrs={"class": "form-control"}),
            "bkash_qr_image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "nagad_number": forms.TextInput(attrs={"class": "form-control"}),
            "nagad_qr_image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "rocket_number": forms.TextInput(attrs={"class": "form-control"}),
            "rocket_qr_image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "bd_payment_terms": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "default_sample_deposit_percentage": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
            "default_bulk_deposit_percentage": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
            "default_bd_sewing_deposit_percentage": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
            "default_currency_na": forms.TextInput(attrs={"class": "form-control"}),
            "default_currency_bd": forms.TextInput(attrs={"class": "form-control"}),
            "default_tax_note": forms.TextInput(attrs={"class": "form-control"}),
            "terms_and_conditions_na": forms.Textarea(attrs={"class": "form-control", "rows": 8}),
            "terms_and_conditions_bd": forms.Textarea(attrs={"class": "form-control", "rows": 8}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_default_currency_na(self):
        return (self.cleaned_data.get("default_currency_na") or "CAD").upper().strip()

    def clean_default_currency_bd(self):
        return (self.cleaned_data.get("default_currency_bd") or "BDT").upper().strip()

    def _clean_percentage(self, field_name, fallback):
        value = self.cleaned_data.get(field_name)
        if value in ("", None):
            return Decimal(fallback)
        value = Decimal(str(value))
        if value < 0:
            return Decimal("0")
        if value > 100:
            return Decimal("100")
        return value

    def clean_default_sample_deposit_percentage(self):
        return self._clean_percentage("default_sample_deposit_percentage", "100.00")

    def clean_default_bulk_deposit_percentage(self):
        return self._clean_percentage("default_bulk_deposit_percentage", "30.00")

    def clean_default_bd_sewing_deposit_percentage(self):
        return self._clean_percentage("default_bd_sewing_deposit_percentage", "30.00")


class InvoicePaymentForm(forms.ModelForm):
    class Meta:
        model = InvoicePayment
        fields = [
            "payment_date",
            "amount",
            "currency",
            "side",
            "payment_method",
            "rate_to_cad",
            "rate_to_bdt",
            "production_order",
            "notes",
        ]
        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "currency": forms.Select(attrs={"class": "form-select"}),
            "side": forms.Select(attrs={"class": "form-select"}),
            "payment_method": forms.Select(attrs={"class": "form-select"}),
            "rate_to_cad": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001", "min": "0"}),
            "rate_to_bdt": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001", "min": "0"}),
            "production_order": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        self.invoice = invoice
        super().__init__(*args, **kwargs)

        if invoice is not None:
            invoice_currency = (getattr(invoice, "currency", "") or "CAD").upper()
            invoice_side = (getattr(invoice, "invoice_region", "") or "").upper()
            if invoice_side not in {"CA", "BD"}:
                invoice_side = "BD" if invoice_currency == "BDT" else "CA"

            self.fields["currency"].initial = invoice_currency
            self.fields["side"].initial = invoice_side

            if getattr(invoice, "order_id", None):
                self.fields["production_order"].initial = invoice.order_id

        self.fields["production_order"].required = False
        self.fields["notes"].required = False

    def clean_amount(self):
        amount = self.cleaned_data.get("amount") or Decimal("0")
        if amount <= 0:
            raise forms.ValidationError("Enter a payment amount greater than zero.")
        return amount

    def clean(self):
        cleaned = super().clean()
        currency = (cleaned.get("currency") or "").upper().strip()
        rate_to_cad = cleaned.get("rate_to_cad") or Decimal("0")
        rate_to_bdt = cleaned.get("rate_to_bdt") or Decimal("0")

        if self.invoice is not None:
            invoice_currency = (self.invoice.currency or "").upper().strip()
            if currency and currency != invoice_currency:
                self.add_error("currency", "Payment currency must match the invoice currency.")

        if currency == "CAD":
            cleaned["rate_to_cad"] = Decimal("1")
            if rate_to_bdt <= 0:
                cad_to_bdt = _latest_rate_bdt_per_cad()
                if cad_to_bdt > 0:
                    cleaned["rate_to_bdt"] = cad_to_bdt
        elif currency == "BDT":
            cleaned["rate_to_bdt"] = Decimal("1")
            if rate_to_cad <= 1:
                self.add_error(
                    "rate_to_cad",
                    "Enter the BDT-per-CAD rate, for example 85. BDT payments are divided by this rate.",
                )
        elif currency == "USD":
            if rate_to_cad <= 0:
                self.add_error("rate_to_cad", "Enter the positive CAD value of one USD.")
            if rate_to_bdt <= 0:
                self.add_error("rate_to_bdt", "Enter the positive BDT value of one USD.")
        else:
            self.add_error("currency", "Select CAD, USD, or BDT.")

        return cleaned
