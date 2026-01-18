from decimal import Decimal
from django import forms

from .models import BDStaff
from .models import (
    AccountingEntry,
    AccountingAttachment,
    AccountingDocument,
    Event,
    Lead,
    Opportunity,
    Customer,
    Shipment,
    InventoryItem,
)
from django.utils import timezone
# --------------------------------------------------
# Shared widgets
# --------------------------------------------------
from django import forms
from .models import Lead


from django import forms
from .models import Lead


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
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_email(self):
        v = (self.cleaned_data.get("email") or "").strip().lower()
        if v and "@" not in v:
            raise forms.ValidationError("Email is not valid.")
        return v

class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        if not data:
            return []

        if isinstance(data, (list, tuple)):
            files = []
            for f in data:
                files.append(super().clean(f, initial))
            return files

        return [super().clean(data, initial)]

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

BD_DAILY_SUBTYPE_CHOICES = [
    ("", "Select subtype"),
    ("FABRIC", "Fabric and materials"),
    ("TRIMS", "Trims and accessories"),
    ("PRINT", "Printing outsourced"),
    ("EMB", "Embroidery or special work"),
    ("UTILITIES", "Electricity and utilities"),
    ("RENT", "Factory rent"),
    ("FOOD", "Tea, snacks, guest food"),
    ("TRANSPORT", "Transport and courier"),
    ("REPAIR", "Machine repair and service"),
    ("OVERTIME", "Staff overtime"),
    ("OTHER", "Other"),
]

# --------------------------------------------------
# Accounting entry form (supports multi attachments)
# --------------------------------------------------
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
            "amount_original": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
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

        return cleaned

    def clean_attachments(self):
        files = self.files.getlist("attachments")
        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed per entry.")
        return files

# --------------------------------------------------
# BD daily entry form (supports multi attachments)
# --------------------------------------------------
# crm/forms.py

from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import AccountingEntry, AccountingAttachment

BD_DAILY_SUBTYPE_CHOICES = [
    ("", "Select subtype"),
    ("FABRIC", "Fabric and materials"),
    ("TRIMS", "Trims and accessories"),
    ("PRINT", "Printing outsourced"),
    ("EMB", "Embroidery or special work"),
    ("UTILITIES", "Electricity and utilities"),
    ("RENT", "Factory rent"),
    ("FOOD", "Tea, snacks, guest food"),
    ("TRANSPORT", "Transport and courier"),
    ("REPAIR", "Machine repair and service"),
    ("OVERTIME", "Staff overtime"),
    ("OTHER", "Other"),
]

class BDDailyEntryForm(forms.ModelForm):
    attachments = forms.FileField(
        required=False,
        widget=MultiFileInput(attrs={"class": "form-control form-control-sm", "multiple": True}),
    )

    class Meta:
        model = AccountingEntry
        fields = [
            "date",
            "sub_type",
            "amount_original",
            "description",
            "attachments",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "sub_type": forms.Select(attrs={"class": "form-select form-select-sm"}, choices=BD_DAILY_SUBTYPE_CHOICES),
            "amount_original": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "description": forms.Textarea(attrs={"rows": 2, "class": "form-control form-control-sm"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # required checks
        self.fields["sub_type"].required = True
        self.fields["amount_original"].required = True
        self.fields["description"].required = True

        if not self.initial.get("date"):
            self.initial["date"] = timezone.localdate()

    def clean_attachments(self):
        files = self.files.getlist("attachments")
        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed.")
        return files

    def save(self, commit=True):
        obj = super().save(commit=False)

        # LOCKED VALUES
        obj.side = "BD"
        obj.direction = "OUT"
        obj.currency = "BDT"
        obj.rate_to_bdt = Decimal("1")
        obj.rate_to_cad = Decimal("0")

        # main type auto
        st = (obj.sub_type or "").strip()
        if st in ["FABRIC", "TRIMS", "PRINT", "EMB"]:
            obj.main_type = "COGS"
        else:
            obj.main_type = "EXPENSE"

        if commit:
            obj.save()
            for f in (self.files.getlist("attachments") or []):
                AccountingAttachment.objects.create(
                    entry=obj,
                    file=f,
                    original_name=(getattr(f, "name", "") or "")[:255],
                    uploaded_by=self.user,
                )

        return obj

from django import forms
from .models import AccountingDocument


class MultiFileInput(forms.FileInput):
    allow_multiple_selected = True


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
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Utilities, Rent, etc"}
        ),
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
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "placeholder": "What is this bill for"}
        ),
    )

    def clean_files(self):
        files = self.files.getlist("files")
        if not files:
            raise forms.ValidationError("Please upload at least one file.")
        if len(files) > 20:
            raise forms.ValidationError("Maximum 20 files allowed.")
        return files


# --------------------------------------------------
# Attach files to an existing accounting entry
# --------------------------------------------------
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
# Other forms
# --------------------------------------------------
# crm/forms.py
from decimal import Decimal
from django import forms

from .models import Shipment

try:
    # only if you have it
    from .models import ExchangeRate
except Exception:
    ExchangeRate = None


def _latest_rate_bdt_per_cad():
    if not ExchangeRate:
        return None
    row = ExchangeRate.objects.order_by("-updated_at").first()
    if not row:
        return None
    return row.cad_to_bdt
from decimal import Decimal
from django import forms
from .models import Shipment


def _shipment_order_field_name():
    field_names = {f.name for f in Shipment._meta.fields}
    if "production_order" in field_names:
        return "production_order"
    if "order" in field_names:
        return "order"
    return None


def _latest_rate_bdt_per_cad():
    """
    Optional helper.
    If you have an ExchangeRate model, this will use it.
    If not, it returns None and the form still works.
    """
    try:
        from .models import ExchangeRate
        row = ExchangeRate.objects.order_by("-updated_at").first()
        if row and row.cad_to_bdt:
            return Decimal(str(row.cad_to_bdt))
    except Exception:
        return None
    return None


ORDER_FIELD = _shipment_order_field_name()
from decimal import Decimal
from django import forms
from .models import Shipment


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
    """
    Optional helper.
    Works even if ExchangeRate does not exist.
    """
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
    # Not a Shipment model field. Only for display or calculation.
    rate_bdt_per_cad = forms.DecimalField(required=False, max_digits=14, decimal_places=4)

    class Meta:
        model = Shipment
        # Only include fields that really exist on Shipment
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

        # Add the correct order FK field if it exists in the model
        if ORDER_FIELD and ORDER_FIELD in SHIPMENT_FIELDS and ORDER_FIELD not in self.fields:
            fk_model = Shipment._meta.get_field(ORDER_FIELD).remote_field.model
            self.fields[ORDER_FIELD] = forms.ModelChoiceField(
                queryset=fk_model.objects.all(),
                required=False,
            )
            # move it to the top
            ordered = [ORDER_FIELD] + [k for k in self.fields.keys() if k != ORDER_FIELD]
            self.order_fields(ordered)

        # Make some fields not required (helps saving)
        for f in ["box_count", "total_weight_kg", "cost_bdt", "cost_cad"]:
            if f in self.fields:
                self.fields[f].required = False

        # Default FX rate
        if not self.initial.get("rate_bdt_per_cad"):
            r = _latest_rate_bdt_per_cad()
            if r is not None:
                self.initial["rate_bdt_per_cad"] = r

    def clean(self):
        cleaned = super().clean()

        rate = cleaned.get("rate_bdt_per_cad")
        cost_bdt = cleaned.get("cost_bdt")
        cost_cad = cleaned.get("cost_cad")

        # Try calculate from costs if rate missing
        if rate in [None, ""] and cost_bdt and cost_cad and Decimal(str(cost_cad)) != 0:
            try:
                cleaned["rate_bdt_per_cad"] = (
                    Decimal(str(cost_bdt)) / Decimal(str(cost_cad))
                ).quantize(Decimal("0.0001"))
            except Exception:
                pass

        # Use latest saved rate if still missing
        if cleaned.get("rate_bdt_per_cad") in [None, ""]:
            r = _latest_rate_bdt_per_cad()
            if r is not None:
                cleaned["rate_bdt_per_cad"] = r

        return cleaned


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = "__all__"
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }


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


from django import forms
from django.utils import timezone

from .models import BDStaff, BDStaffMonth


class BDStaffForm(forms.ModelForm):
    class Meta:
        model = BDStaff
        fields = ["name", "role", "base_salary_bdt", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Staff name"}),
            "role": forms.TextInput(attrs={"placeholder": "Role"}),
            "base_salary_bdt": forms.NumberInput(attrs={"step": "0.01"}),
            "is_active": forms.CheckboxInput(),
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
            "base_salary_bdt": forms.NumberInput(attrs={"step": "0.01"}),
            "overtime_hours": forms.NumberInput(attrs={"step": "0.01"}),
            "overtime_rate_bdt": forms.NumberInput(attrs={"step": "0.01"}),
            "bonus_bdt": forms.NumberInput(attrs={"step": "0.01"}),
            "deduction_bdt": forms.NumberInput(attrs={"step": "0.01"}),
            "is_paid": forms.CheckboxInput(),
            "paid_date": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # paid_date should not block saving
        if "paid_date" in self.fields:
            self.fields["paid_date"].required = False

        # safety: if any extra field exists in model and is required in DB,
        # this prevents form save from failing if it shows up later
        if "rate_bdt_per_cad" in self.fields:
            self.fields["rate_bdt_per_cad"].required = False

    def clean(self):
        cleaned = super().clean()

        is_paid = cleaned.get("is_paid")
        paid_date = cleaned.get("paid_date")

        if is_paid and not paid_date:
            cleaned["paid_date"] = timezone.localdate()

        if not is_paid:
            cleaned["paid_date"] = None

        return cleaned

        return cleaned

    from django import forms
    from .models import Invoice

from decimal import Decimal
from django import forms
from .models import Invoice


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
            "invoice_number": forms.TextInput(attrs={"placeholder": "Auto if blank"}),
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),

            "subtotal": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "shipping_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "discount_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "tax_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "total_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0", "readonly": "readonly"}),
            "paid_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),

            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Make invoice_number optional (auto generated in view)
        self.fields["invoice_number"].required = False

        # Make total_amount read only (auto calculated in view)
        self.fields["total_amount"].required = False
        self.fields["total_amount"].disabled = True

        # Status can be auto adjusted by view, but keep it editable
        self.fields["status"].required = False

    def _clean_money(self, field_name: str) -> Decimal:
        v = self.cleaned_data.get(field_name)
        if v in ("", None):
            return Decimal("0")
        try:
            return Decimal(v)
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