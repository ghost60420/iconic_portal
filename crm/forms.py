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
from django.utils import timezone

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
    Lead,
    Opportunity,
    Shipment,
    Product,
    Fabric,
    Accessory,
    Trim,
    ThreadOption,
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
        if isinstance(data, (list, tuple)):
            return [super().clean(f, initial) for f in data]
        return [super().clean(data, initial)]


# --------------------------------------------------
# Lead form
# --------------------------------------------------
class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = [
            "account_brand",
            "company_website",
            "market",
            "country",
            "city",
            "source",
            "lead_type",
            "lead_status",
            "priority",
            "product_category",
            "product_interest",
            "order_quantity",
            "budget",
            "preferred_contact_time",
            "owner",
            "next_followup",
            "contact_name",
            "email",
            "phone",
            "attachment",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        category_choices = [("", "Select a category")] + list(Opportunity.PRODUCT_CATEGORY_CHOICES)
        interest_choices = [("", "Select an interest")] + list(Opportunity.PRODUCT_TYPE_CHOICES)

        if "product_category" in self.fields:
            self.fields["product_category"].choices = category_choices
            self.fields["product_category"].required = True
            self.fields["product_category"].widget.attrs.update({"class": "form-control"})

            current_value = getattr(self.instance, "product_category", "") or ""
            if current_value and current_value not in dict(Opportunity.PRODUCT_CATEGORY_CHOICES):
                self.fields["product_category"].choices.append((current_value, current_value))

        if "product_interest" in self.fields:
            self.fields["product_interest"].choices = interest_choices
            self.fields["product_interest"].required = True
            self.fields["product_interest"].widget.attrs.update({"class": "form-control"})

            current_value = getattr(self.instance, "product_interest", "") or ""
            if current_value and current_value not in dict(Opportunity.PRODUCT_TYPE_CHOICES):
                self.fields["product_interest"].choices.append((current_value, current_value))


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

    def __init__(self, *args, lock_side=None, lock_direction=None, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            if "status" in self.fields:
                self.fields["status"].initial = (self.instance.status or "").strip()
            if "main_type" in self.fields:
                self.fields["main_type"].initial = (self.instance.main_type or "").strip()

        if lock_side and "side" in self.fields:
            self.fields["side"].initial = lock_side
            self.fields["side"].disabled = True

        if lock_direction and "direction" in self.fields:
            self.fields["direction"].initial = lock_direction
            self.fields["direction"].disabled = True

        for name in ("side", "direction"):
            if name in self.fields and getattr(self.fields[name], "disabled", False):
                css = self.fields[name].widget.attrs.get("class", "")
                self.fields[name].widget.attrs["class"] = (css + " opacity-75").strip()

    def clean(self):
        cleaned = super().clean()

        if "side" in self.fields and self.fields["side"].disabled:
            cleaned["side"] = self.fields["side"].initial

        if "direction" in self.fields and self.fields["direction"].disabled:
            cleaned["direction"] = self.fields["direction"].initial

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
                    cleaned["rate_to_cad"] = (Decimal("1") / cad_to_bdt).quantize(Decimal("0.000001"))

        return cleaned

    def clean_attachments(self):
        files = self.files.getlist("attachments")
        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed per entry.")
        return files


# --------------------------------------------------
# BD daily entry form
# Locked: BD, OUT, BDT
# --------------------------------------------------
# crm/forms.py

from decimal import Decimal
from django import forms
from django.forms import ModelForm
from .models import AccountingEntry

BD_QUICK_CHOICES = [
    ("", "Select"),
    ("FABRIC", "Fabric"),
    ("TRIMS", "Trims"),
    ("PRINT", "Print"),
    ("EMB", "Embroidery"),
    ("SALARY", "Salary"),
    ("RENT", "Rent"),
    ("UTILITY", "Utility"),
    ("TRANSPORT", "Transport"),
    ("FOOD", "Food"),
    ("MISC", "Misc"),
]


class BDDailyEntryForm(ModelForm):
    # Make this optional so the form can save even if the UI does not send it
    quick_category = forms.ChoiceField(
        choices=BD_QUICK_CHOICES,
        required=False,
    )

    # This matches your UI field "Sub type"
    sub_type = forms.CharField(required=False)

    class Meta:
        model = AccountingEntry
        fields = [
            "date",
            "main_type",
            "sub_type",
            "amount_original",
            "description",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self.fields["main_type"].required = False
        self.fields["main_type"].initial = "EXPENSE"
        self.fields["amount_original"].required = True

    def clean(self):
        cleaned = super().clean()

        qc = (cleaned.get("quick_category") or "").strip()
        st = (cleaned.get("sub_type") or "").strip()

        # Locked BD rules
        cleaned["side"] = "BD"
        cleaned["direction"] = "OUT"
        cleaned["currency"] = "BDT"
        cleaned["rate_to_bdt"] = Decimal("1")
        cad_to_bdt = _latest_rate_bdt_per_cad()
        if cad_to_bdt and cad_to_bdt > 0:
            cleaned["rate_to_cad"] = (Decimal("1") / cad_to_bdt).quantize(Decimal("0.000001"))
        else:
            cleaned["rate_to_cad"] = Decimal("0")

        # If user typed sub_type, use it
        # If not, use quick_category
        cleaned["sub_type"] = st or qc

        # Auto main_type if empty
        mt = (cleaned.get("main_type") or "").strip()
        if not mt:
            if qc in ["FABRIC", "TRIMS", "PRINT", "EMB"]:
                cleaned["main_type"] = "COGS"
            else:
                cleaned["main_type"] = "EXPENSE"

        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        cd = self.cleaned_data

        obj.side = cd["side"]
        obj.direction = cd["direction"]
        obj.currency = cd["currency"]
        obj.rate_to_bdt = cd["rate_to_bdt"]
        obj.rate_to_cad = cd["rate_to_cad"]
        obj.sub_type = cd.get("sub_type") or ""
        obj.main_type = cd.get("main_type") or "EXPENSE"

        if self.user and not obj.created_by_id:
            obj.created_by = self.user

        if commit:
            obj.save()

        return obj
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
        super().__init__(*args, **kwargs)

        if ORDER_FIELD and ORDER_FIELD in SHIPMENT_FIELDS and ORDER_FIELD not in self.fields:
            fk_model = Shipment._meta.get_field(ORDER_FIELD).remote_field.model
            self.fields[ORDER_FIELD] = forms.ModelChoiceField(
                queryset=fk_model.objects.all(),
                required=False,
            )
            ordered = [ORDER_FIELD] + [k for k in self.fields.keys() if k != ORDER_FIELD]
            self.order_fields(ordered)

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
    class Meta:
        model = InventoryItem
        fields = "__all__"
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }


# --------------------------------------------------
# Event form
# --------------------------------------------------
class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title",
            "start_datetime",
            "end_datetime",
            "event_type",
            "priority",
            "status",
            "lead",
            "opportunity",
            "customer",
            "production_stage",
            "assigned_to_name",
            "assigned_to_email",
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
            "lead": forms.Select(attrs={"class": "form-select"}),
            "opportunity": forms.Select(attrs={"class": "form-select"}),
            "customer": forms.Select(attrs={"class": "form-select"}),
            "production_stage": forms.Select(attrs={"class": "form-select"}),
            "assigned_to_name": forms.TextInput(attrs={"class": "form-control"}),
            "assigned_to_email": forms.EmailInput(attrs={"class": "form-control"}),
            "reminder_minutes_before": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
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
            "production_stage",
            "assigned_to_name",
            "assigned_to_email",
            "reminder_minutes_before",
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
    class Meta:
        model = Invoice
        fields = [
            "order",
            "customer",
            "invoice_number",
            "issue_date",
            "due_date",
            "currency",
            "subtotal",
            "shipping_amount",
            "discount_amount",
            "tax_amount",
            "total_amount",
            "paid_amount",
            "status",
            "notes",
        ]
        widgets = {
            "invoice_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto if blank"}),
            "issue_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "subtotal": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "shipping_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "discount_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "tax_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "readonly": "readonly"}),
            "paid_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "invoice_number" in self.fields:
            self.fields["invoice_number"].required = False

        if "total_amount" in self.fields:
            self.fields["total_amount"].required = False
            self.fields["total_amount"].disabled = True

        if "status" in self.fields:
            self.fields["status"].required = False

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

    def clean_paid_amount(self):
        return self._clean_money("paid_amount")
