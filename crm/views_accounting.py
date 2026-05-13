# crm/views_accounting.py (or wherever your accounting views live)

import csv
import io
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import BDStaff, BDStaffMonth
from .forms import BDStaffForm, BDStaffMonthForm
from collections import defaultdict
from decimal import Decimal
from uuid import uuid4
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from .decorators import bd_required
from .models import AccountingEntry
from openpyxl import Workbook
import csv
from decimal import Decimal
from django.http import HttpResponse
from django.db.models import Q
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import (
    AccountingEntry,
    AccountingAttachment,
    AccountingEntryAudit,
    AccountingDocument,
    ExchangeRate,
    BDStaff,
    BDStaffMonth,
    Customer,
    Invoice,
    InvoicePayment,
    InventoryItem,
    Product,
    ProductionOrder,
)

from .forms import (
    AccountingEntryForm,
    AccountingEntryAttachForm,
    AccountingDocumentForm,
    BDDailyEntryForm,
    BDStaffForm,
    BDStaffMonthForm,
)

try:
    from .models import AccountingMonthLock
except Exception:
    AccountingMonthLock = None


# --------------------
# PERMISSIONS
# --------------------
def _in_group(user, name: str) -> bool:
    return bool(user and user.is_authenticated and user.groups.filter(name=name).exists())


def is_ca_user(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or _in_group(user, "CA") or _in_group(user, "Canada")


def is_bd_user(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or _in_group(user, "BD") or _in_group(user, "Bangladesh")


ca_required = user_passes_test(is_ca_user, login_url="/login/")
bd_required = user_passes_test(is_bd_user, login_url="/login/")


def user_is_bd_only(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return False
    return user.groups.filter(name__in=["BD", "Bangladesh"]).exists() and not user.groups.filter(
        name__in=["CA", "Canada"]
    ).exists()


def can_edit_entry(user, entry) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    side = (getattr(entry, "side", "") or "").upper().strip()
    if is_ca_user(user) and side == "CA":
        return True
    if is_bd_user(user) and side == "BD":
        return True
    return False


def can_delete_entry(user, entry) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


# --------------------
# SMALL HELPERS
# --------------------
def _parse_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _get_rate_row():
    row = ExchangeRate.objects.order_by("-updated_at").first()
    if not row:
        row = ExchangeRate.objects.create(cad_to_bdt=Decimal("0"))
    return row


def _entry_snapshot(e: AccountingEntry) -> dict:
    return {
        "id": e.id,
        "date": str(e.date) if e.date else "",
        "side": e.side,
        "direction": e.direction,
        "status": e.status,
        "main_type": e.main_type,
        "sub_type": e.sub_type,
        "currency": e.currency,
        "amount_original": str(e.amount_original) if e.amount_original is not None else "",
        "amount_cad": str(e.amount_cad) if e.amount_cad is not None else "",
        "amount_bdt": str(e.amount_bdt) if e.amount_bdt is not None else "",
        "transfer_ref": e.transfer_ref or "",
        "description": e.description or "",
        "internal_note": e.internal_note or "",
        "customer_id": e.customer_id or "",
        "opportunity_id": e.opportunity_id or "",
        "production_order_id": e.production_order_id or "",
        "shipment_id": e.shipment_id or "",
    }


def _audit(entry: AccountingEntry, action: str, user, before=None, after=None, note=""):
    try:
        AccountingEntryAudit.objects.create(
            entry=entry,
            action=action,
            changed_by=user if (user and user.is_authenticated) else None,
            before_data=before,
            after_data=after,
            note=note or "",
        )
    except Exception:
        pass


def _save_attachments(entry, request, user, field_name="attachments") -> int:
    files = request.FILES.getlist(field_name)
    if not files:
        return 0

    saved = 0
    for f in files:
        AccountingAttachment.objects.create(
            entry=entry,
            file=f,
            uploaded_by=user if user and user.is_authenticated else None,
            original_name=(getattr(f, "name", "") or "")[:255],
        )
        saved += 1
    return saved


# --------------------
# EXPORT HELPERS
# --------------------
def _entries_queryset_from_request(request, force_side=None):
    qs = AccountingEntry.objects.all().select_related(
        "customer", "opportunity", "production_order", "shipment", "created_by"
    )

    side = (force_side or request.GET.get("side") or "").strip()
    direction = (request.GET.get("direction") or "").strip()
    status = (request.GET.get("status") or "").strip()
    main_type = (request.GET.get("main_type") or "").strip()

    year = _parse_int(request.GET.get("year"))
    month = _parse_int(request.GET.get("month"))

    if side:
        qs = qs.filter(side=side)
    if direction:
        qs = qs.filter(direction=direction)
    if status:
        qs = qs.filter(status=status)
    if main_type:
        qs = qs.filter(main_type=main_type)
    if year:
        qs = qs.filter(date__year=year)
    if month:
        qs = qs.filter(date__month=month)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(description__icontains=q)
            | Q(sub_type__icontains=q)
            | Q(internal_note__icontains=q)
            | Q(transfer_ref__icontains=q)
        )

    return qs.order_by("-date", "-id")


def _export_headers():
    return [
        "ID",
        "Date",
        "Side",
        "Direction",
        "Status",
        "Main type",
        "Sub type",
        "Currency",
        "Amount original",
        "Rate to CAD",
        "Rate to BDT",
        "Amount CAD",
        "Amount BDT",
        "Customer",
        "Opportunity ID",
        "Production order",
        "Shipment ID",
        "Description",
        "Internal note",
        "Created by",
        "Created at",
    ]


def _entry_export_row(e: AccountingEntry):
    return [
        e.id,
        e.date.isoformat() if e.date else "",
        e.side,
        e.direction,
        e.status,
        e.main_type,
        e.sub_type or "",
        e.currency,
        str(e.amount_original or ""),
        str(getattr(e, "rate_to_cad", "") or ""),
        str(getattr(e, "rate_to_bdt", "") or ""),
        str(e.amount_cad or ""),
        str(e.amount_bdt or ""),
        (e.customer.name if getattr(e.customer, "name", None) else ""),
        str(e.opportunity_id or ""),
        (e.production_order.order_code if getattr(e.production_order, "order_code", None) else ""),
        str(e.shipment_id or ""),
        e.description or "",
        e.internal_note or "",
        (e.created_by.username if e.created_by else ""),
        e.created_at.isoformat() if e.created_at else "",
    ]


def _write_xlsx(qs, filename):
    wb = Workbook()
    ws = wb.active
    ws.title = "Entries"
    ws.append(_export_headers())
    for e in qs:
        ws.append(_entry_export_row(e))

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    resp = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# --------------------
# SEND MONEY FORM
# --------------------
SENT_METHOD_CHOICES = [
    ("BANK", "Bank"),
    ("APP", "Online app"),
    ("CASH", "Cash"),
]


class SendMoneyToBdForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    cad_amount = forms.DecimalField(max_digits=14, decimal_places=2)
    sent_method = forms.ChoiceField(choices=SENT_METHOD_CHOICES)
    note = forms.CharField(required=False)


# --------------------
# ROUTER ADD PAGE
# --------------------
@login_required
def accounting_entry_add(request):
    if user_is_bd_only(request.user):
        return redirect("accounting_entry_add_bd")

    side = (request.GET.get("side") or "").upper().strip()
    if side == "BD":
        return redirect("accounting_entry_add_bd")
    return redirect("accounting_entry_add_ca")


# --------------------
# ADD CA
# --------------------
@login_required
@ca_required
def accounting_entry_add_ca(request):
    LOCK_SIDE = "CA"
    LOCK_DIRECTION = "IN"
    LOCK_CURRENCY = "CAD"

    if request.method == "POST":
        form = AccountingEntryForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.side = LOCK_SIDE
            obj.direction = LOCK_DIRECTION
            obj.currency = LOCK_CURRENCY
            rate_row = _get_rate_row()
            cad_to_bdt = rate_row.cad_to_bdt if rate_row else Decimal("0")
            if not obj.rate_to_cad or obj.rate_to_cad <= 0:
                obj.rate_to_cad = Decimal("1")
            if cad_to_bdt and cad_to_bdt > 0 and (not obj.rate_to_bdt or obj.rate_to_bdt <= 0):
                obj.rate_to_bdt = cad_to_bdt
            obj.created_by = request.user
            obj.save()
            form.save_m2m()

            _save_attachments(obj, request, request.user, "attachments")
            _audit(obj, "CREATE", request.user, after=_entry_snapshot(obj), note="CA add")

            messages.success(request, "Canada entry added.")
            return redirect("accounting_entry_list")

        messages.error(request, "Please fix the errors below.")
    else:
        form = AccountingEntryForm(
            initial={"side": LOCK_SIDE, "direction": LOCK_DIRECTION, "currency": LOCK_CURRENCY}
        )

    return render(
        request,
        "crm/accounting_entry_add_ca.html",
        {
            "form": form,
            "lock_side": LOCK_SIDE,
            "lock_direction": LOCK_DIRECTION,
            "lock_currency": LOCK_CURRENCY,
            "lock_mode": "CA_IN_CAD",
        },
    )


# --------------------
# ADD BD
# --------------------
@login_required
@bd_required
def accounting_entry_add_bd(request):
    LOCK_SIDE = "BD"
    LOCK_CURRENCY = "BDT"

    if request.method == "POST":
        form = AccountingEntryForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)

            obj.side = LOCK_SIDE
            obj.currency = LOCK_CURRENCY

            if not obj.direction:
                obj.direction = (request.POST.get("direction") or "").strip()

            rate_row = _get_rate_row()
            cad_to_bdt = rate_row.cad_to_bdt if rate_row else Decimal("0")
            if not obj.rate_to_bdt or obj.rate_to_bdt <= 0:
                obj.rate_to_bdt = Decimal("1")
            if cad_to_bdt and cad_to_bdt > 0 and (not obj.rate_to_cad or obj.rate_to_cad <= 0):
                obj.rate_to_cad = (Decimal("1") / cad_to_bdt).quantize(Decimal("0.000001"))
            obj.created_by = request.user
            obj.save()
            form.save_m2m()

            _save_attachments(obj, request, request.user, "attachments")
            _audit(obj, "CREATE", request.user, after=_entry_snapshot(obj), note="BD add")

            messages.success(request, "Bangladesh entry added.")
            return redirect("accounting_bd_grid")

        messages.error(request, "Please fix the errors below.")
    else:
        form = AccountingEntryForm(initial={"side": LOCK_SIDE, "currency": LOCK_CURRENCY})

    return render(
        request,
        "crm/accounting_entry_add_bd.html",
        {"form": form, "lock_side": LOCK_SIDE, "lock_currency": LOCK_CURRENCY, "lock_mode": "BD_ADD"},
    )


# --------------------
# EDIT AND DELETE
# --------------------
@login_required
def accounting_entry_edit(request, pk):
    entry = get_object_or_404(AccountingEntry, pk=pk)
    if not can_edit_entry(request.user, entry):
        return HttpResponseForbidden("You do not have permission to edit this entry.")

    if request.method == "POST":
        form = AccountingEntryForm(
            request.POST,
            request.FILES,
            instance=entry,
            lock_side=entry.side,
            lock_direction=entry.direction,
        )
        if form.is_valid():
            obj = form.save(commit=False)
            obj.side = entry.side
            obj.direction = entry.direction
            obj.save()
            form.save_m2m()

            _save_attachments(obj, request, request.user, "attachments")
            _audit(entry, "UPDATE", request.user, after=_entry_snapshot(obj), note="Edit")

            messages.success(request, "Updated.")
            return redirect("accounting_entry_list")

        messages.error(request, "Please fix the errors below.")
    else:
        form = AccountingEntryForm(instance=entry, lock_side=entry.side, lock_direction=entry.direction)

    return render(request, "crm/accounting_edit.html", {"form": form, "entry": entry})


@login_required
def accounting_entry_delete(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("Delete must be POST.")

    entry = get_object_or_404(AccountingEntry, pk=pk)
    if not can_delete_entry(request.user, entry):
        return HttpResponseForbidden("You do not have permission to delete this entry.")

    entry.delete()
    messages.success(request, "Deleted.")
    return redirect("accounting_entry_list")


# --------------------
# HOME
# --------------------
@login_required
def accounting_home(request):
    if is_ca_user(request.user):
        return redirect("accounting_ca_master")
    if is_bd_user(request.user):
        return redirect("accounting_bd_daily")
    return HttpResponseForbidden("Access denied.")


AP_CATEGORY_RULES = [
    ("fabric_suppliers", "Fabric suppliers", ["fabric", "yarn", "dye", "knit", "mill"]),
    ("trim_suppliers", "Trim suppliers", ["trim", "label", "tag", "button", "zip", "thread"]),
    ("packaging_suppliers", "Packaging suppliers", ["pack", "poly", "carton", "box", "hanger"]),
    ("freight_customs", "Freight and customs", ["freight", "custom", "duty", "courier", "shipping", "truck", "transport"]),
    ("factories", "Factories", ["factory", "sewing", "cutting", "finishing", "print", "embro", "wash"]),
    ("contractors", "Contractors", ["contract", "subcontract", "consult", "freelance", "service"]),
    ("utilities", "Utilities", ["electric", "utility", "water", "gas", "internet", "phone"]),
    ("office_expenses", "Office expenses", ["office", "rent", "salary", "software", "food", "repair", "maintenance"]),
]


AP_STATUS_OPTIONS = [
    ("", "All statuses"),
    ("unpaid", "Unpaid"),
    ("partial", "Partially paid"),
    ("paid", "Paid"),
    ("overdue", "Overdue"),
]


def _parse_ap_date(value):
    value = (value or "").strip()
    return parse_date(value) if value else None


def _ap_decimal(value):
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def _ap_status_key(entry, today=None):
    today = today or timezone.localdate()
    status = (entry.status or "").upper().strip()
    if status == "PAID":
        return "paid"
    if status == "PARTIAL":
        return "overdue" if entry.date and entry.date < today else "partial"
    if status in {"CANCELLED", "VOID"}:
        return "cancelled"
    if entry.date and entry.date < today:
        return "overdue"
    return "unpaid"


def _ap_status_label(key):
    return {
        "unpaid": "Unpaid",
        "partial": "Partially paid",
        "paid": "Paid",
        "overdue": "Overdue",
        "cancelled": "Cancelled",
    }.get(key, "Unpaid")


def _ap_category(entry):
    main_type = (entry.main_type or "").strip()
    sub_type = (entry.sub_type or "").strip()
    text = f"{main_type} {sub_type} {entry.description or ''} {entry.internal_note or ''}".lower()
    for key, label, needles in AP_CATEGORY_RULES:
        if any(needle in text for needle in needles):
            return key, label
    if main_type == "COGS":
        return "factories", "Factories"
    if main_type == "TAX":
        return "freight_customs", "Freight and customs"
    if main_type == "EXPENSE":
        return "office_expenses", "Office expenses"
    return "other", "Other vendors"


def _ap_supplier_label(entry):
    if entry.production_order_id and entry.production_order:
        return f"Factory / {entry.production_order.order_code or entry.production_order.title or entry.production_order_id}"
    if entry.shipment_id and entry.shipment:
        tracking = getattr(entry.shipment, "tracking_number", "") or ""
        carrier = getattr(entry.shipment, "carrier", "") or ""
        if carrier or tracking:
            return f"Freight / {carrier or 'Shipment'} {tracking}".strip()
        return f"Freight / Shipment {entry.shipment_id}"
    if entry.customer_id and entry.customer:
        name = getattr(entry.customer, "account_brand", "") or getattr(entry.customer, "contact_name", "")
        if name:
            return f"Customer linked / {name}"
    if entry.sub_type:
        return entry.sub_type.strip()
    description = (entry.description or "").strip()
    if description:
        return description.split("|")[0].split("-")[0][:80].strip() or "Unassigned vendor"
    return "Unassigned vendor"


def _ap_apply_db_filters(qs, filters):
    if filters["date_from"]:
        qs = qs.filter(date__gte=filters["date_from"])
    if filters["date_to"]:
        qs = qs.filter(date__lte=filters["date_to"])
    if filters["currency"]:
        qs = qs.filter(currency=filters["currency"])
    if filters["side"]:
        qs = qs.filter(side=filters["side"])
    return qs


def _ap_currency_totals(rows, amount_key):
    totals = {}
    for row in rows:
        currency = row.get("currency") or "Unknown"
        totals[currency] = totals.get(currency, Decimal("0")) + _ap_decimal(row.get(amount_key))
    return [
        {"currency": currency, "amount": amount}
        for currency, amount in sorted(totals.items())
        if amount != 0
    ]


def _ap_row(entry, today):
    category_key, category_label = _ap_category(entry)
    status_key = _ap_status_key(entry, today)
    amount = _ap_decimal(entry.amount_original)
    paid_amount = amount if status_key == "paid" else Decimal("0")
    outstanding = Decimal("0") if status_key == "paid" else amount
    return {
        "entry": entry,
        "supplier": _ap_supplier_label(entry),
        "category_key": category_key,
        "category_label": category_label,
        "status_key": status_key,
        "status_label": _ap_status_label(status_key),
        "currency": (entry.currency or "Unknown").upper(),
        "amount": amount,
        "paid_amount": paid_amount,
        "outstanding": outstanding,
        "days_overdue": (today - entry.date).days if entry.date and entry.date < today and status_key != "paid" else 0,
    }


@login_required
def accounts_payable_dashboard(request):
    today = timezone.localdate()
    filters = {
        "date_from": _parse_ap_date(request.GET.get("date_from")),
        "date_to": _parse_ap_date(request.GET.get("date_to")),
        "supplier": (request.GET.get("supplier") or "").strip(),
        "status": (request.GET.get("status") or "").strip(),
        "currency": (request.GET.get("currency") or "").strip().upper(),
        "side": (request.GET.get("side") or "").strip().upper(),
        "category": (request.GET.get("category") or "").strip(),
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "supplier": filters["supplier"],
        "status": filters["status"],
        "currency": filters["currency"],
        "side": filters["side"],
        "category": filters["category"],
    }

    base_qs = (
        AccountingEntry.objects.filter(direction=AccountingEntry.DIR_OUT)
        .exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .select_related("customer", "opportunity", "production_order", "shipment", "created_by")
    )
    supplier_choices = sorted({_ap_supplier_label(entry) for entry in base_qs[:1000]})

    qs = _ap_apply_db_filters(base_qs, filters).order_by("date", "-id")
    rows = [_ap_row(entry, today) for entry in qs[:1000]]
    if filters["supplier"]:
        rows = [row for row in rows if row["supplier"] == filters["supplier"]]
    if filters["status"]:
        rows = [row for row in rows if row["status_key"] == filters["status"]]
    if filters["category"]:
        rows = [row for row in rows if row["category_key"] == filters["category"]]

    open_rows = [row for row in rows if row["status_key"] != "paid"]
    paid_rows = [row for row in rows if row["status_key"] == "paid"]
    overdue_rows = [row for row in rows if row["status_key"] == "overdue"]
    due_week_rows = [
        row for row in open_rows
        if row["entry"].date and today <= row["entry"].date <= today + timedelta(days=7)
    ]
    due_month_rows = [
        row for row in open_rows
        if row["entry"].date and row["entry"].date.year == today.year and row["entry"].date.month == today.month
    ]

    total_bills = sum((row["amount"] for row in rows), Decimal("0"))
    total_paid = sum((row["paid_amount"] for row in rows), Decimal("0"))
    total_outstanding = sum((row["outstanding"] for row in open_rows), Decimal("0"))
    due_week_total = sum((row["outstanding"] for row in due_week_rows), Decimal("0"))
    due_month_total = sum((row["outstanding"] for row in due_month_rows), Decimal("0"))
    overdue_total = sum((row["outstanding"] for row in overdue_rows), Decimal("0"))

    supplier_map = {}
    for row in open_rows:
        key = (row["supplier"], row["currency"])
        if key not in supplier_map:
            supplier_map[key] = {
                "supplier": row["supplier"],
                "currency": row["currency"],
                "bill_count": 0,
                "overdue_count": 0,
                "outstanding": Decimal("0"),
            }
        supplier_map[key]["bill_count"] += 1
        supplier_map[key]["overdue_count"] += 1 if row["status_key"] == "overdue" else 0
        supplier_map[key]["outstanding"] += row["outstanding"]
    supplier_balance_rows = sorted(
        supplier_map.values(),
        key=lambda item: item["outstanding"],
        reverse=True,
    )[:30]

    monthly_map = {}
    for row in rows:
        entry = row["entry"]
        if not entry.date:
            continue
        key = entry.date.strftime("%Y-%m")
        if key not in monthly_map:
            monthly_map[key] = {
                "key": key,
                "label": entry.date.strftime("%b %Y"),
                "total": Decimal("0"),
                "paid": Decimal("0"),
                "outstanding": Decimal("0"),
                "count": 0,
            }
        monthly_map[key]["total"] += row["amount"]
        monthly_map[key]["paid"] += row["paid_amount"]
        monthly_map[key]["outstanding"] += row["outstanding"]
        monthly_map[key]["count"] += 1
    monthly_rows = [monthly_map[key] for key in sorted(monthly_map.keys())][-12:]
    max_monthly = max([row["total"] for row in monthly_rows] or [Decimal("0")])
    for row in monthly_rows:
        row["bar_percent"] = int((row["total"] / max_monthly) * 100) if max_monthly > 0 else 0

    category_rows = []
    category_map = {}
    for row in open_rows:
        key = (row["category_key"], row["category_label"], row["currency"])
        if key not in category_map:
            category_map[key] = {
                "category": row["category_label"],
                "currency": row["currency"],
                "outstanding": Decimal("0"),
                "bill_count": 0,
            }
        category_map[key]["outstanding"] += row["outstanding"]
        category_map[key]["bill_count"] += 1
    category_rows = sorted(category_map.values(), key=lambda item: item["outstanding"], reverse=True)

    return render(
        request,
        "crm/accounting_accounts_payable_dashboard.html",
        {
            "filter_values": filter_values,
            "supplier_choices": supplier_choices,
            "status_options": AP_STATUS_OPTIONS,
            "currency_options": ["CAD", "BDT", "USD"],
            "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
            "category_options": [(key, label) for key, label, _needles in AP_CATEGORY_RULES] + [("other", "Other vendors")],
            "total_bills": total_bills,
            "total_paid": total_paid,
            "total_outstanding": total_outstanding,
            "total_by_currency": _ap_currency_totals(rows, "amount"),
            "paid_by_currency": _ap_currency_totals(rows, "paid_amount"),
            "outstanding_by_currency": _ap_currency_totals(open_rows, "outstanding"),
            "overdue_count": len(overdue_rows),
            "due_week_count": len(due_week_rows),
            "due_month_count": len(due_month_rows),
            "bill_count": len(rows),
            "paid_count": len(paid_rows),
            "open_count": len(open_rows),
            "overdue_total": overdue_total,
            "due_week_total": due_week_total,
            "due_month_total": due_month_total,
            "outstanding_rows": open_rows[:75],
            "payment_rows": paid_rows[:75],
            "due_soon_rows": sorted(due_week_rows + overdue_rows, key=lambda row: row["entry"].date or today)[:40],
            "supplier_balance_rows": supplier_balance_rows,
            "monthly_rows": monthly_rows,
            "category_rows": category_rows[:12],
        },
    )


PL_OPEX_TYPES = {"EXPENSE", "TAX", "OTHER"}


def _parse_pl_date(value):
    value = (value or "").strip()
    return parse_date(value) if value else None


def _pl_decimal(value):
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def _pl_amount_cad(entry):
    amount_cad = _pl_decimal(getattr(entry, "amount_cad", None))
    if amount_cad:
        return amount_cad
    currency = (entry.currency or "").upper().strip()
    if currency == "CAD":
        return _pl_decimal(entry.amount_original)
    return Decimal("0")


def _pl_customer_label(entry):
    if entry.customer_id and entry.customer:
        return entry.customer.account_brand or entry.customer.contact_name or f"Customer {entry.customer_id}"
    if entry.production_order_id and entry.production_order and entry.production_order.customer_id:
        customer = entry.production_order.customer
        return customer.account_brand or customer.contact_name or f"Customer {customer.pk}"
    return "Unassigned customer"


def _pl_product_category(entry):
    if entry.production_order_id and entry.production_order:
        product = entry.production_order.product
        if product and product.product_category:
            return product.product_category.strip() or "Uncategorized"
        opportunity = entry.production_order.opportunity
        if opportunity and opportunity.product_category:
            return opportunity.product_category.strip() or "Uncategorized"
    if entry.opportunity_id and entry.opportunity and entry.opportunity.product_category:
        return entry.opportunity.product_category.strip() or "Uncategorized"
    return "Uncategorized"


def _pl_cost_category(entry):
    sub_type = (entry.sub_type or "").strip()
    if sub_type:
        return sub_type
    main_type = (entry.main_type or "").strip()
    return main_type or "Uncategorized"


def _pl_side_label(side):
    return {"CA": "Canada", "BD": "Bangladesh"}.get((side or "").upper(), side or "Unknown")


def _pl_row(entry):
    return {
        "entry": entry,
        "amount": _pl_decimal(entry.amount_original),
        "amount_cad": _pl_amount_cad(entry),
        "currency": (entry.currency or "Unknown").upper(),
        "main_type": (entry.main_type or "").upper().strip(),
        "customer": _pl_customer_label(entry),
        "product_category": _pl_product_category(entry),
        "cost_category": _pl_cost_category(entry),
        "side": (entry.side or "").upper().strip(),
        "side_label": _pl_side_label(entry.side),
    }


def _pl_currency_totals(rows):
    totals = {}
    for row in rows:
        currency = row["currency"]
        totals[currency] = totals.get(currency, Decimal("0")) + row["amount"]
    return [
        {"currency": currency, "amount": amount}
        for currency, amount in sorted(totals.items())
        if amount != 0
    ]


def _pl_group(rows, key, amount_key="amount_cad", limit=20):
    grouped = {}
    for row in rows:
        label = row.get(key) or "Uncategorized"
        if label not in grouped:
            grouped[label] = {"label": label, "amount": Decimal("0"), "count": 0}
        grouped[label]["amount"] += _pl_decimal(row.get(amount_key))
        grouped[label]["count"] += 1
    return sorted(grouped.values(), key=lambda item: item["amount"], reverse=True)[:limit]


def _pl_monthly_rows(rows):
    monthly = {}
    for row in rows:
        entry = row["entry"]
        if not entry.date:
            continue
        key = entry.date.strftime("%Y-%m")
        if key not in monthly:
            monthly[key] = {
                "key": key,
                "label": entry.date.strftime("%b %Y"),
                "revenue": Decimal("0"),
                "cogs": Decimal("0"),
                "opex": Decimal("0"),
                "net": Decimal("0"),
            }
        main_type = row["main_type"]
        direction = (entry.direction or "").upper().strip()
        amount = row["amount_cad"]
        if direction == AccountingEntry.DIR_IN and main_type == "INCOME":
            monthly[key]["revenue"] += amount
        elif direction == AccountingEntry.DIR_OUT and main_type == "COGS":
            monthly[key]["cogs"] += amount
        elif direction == AccountingEntry.DIR_OUT and main_type in PL_OPEX_TYPES:
            monthly[key]["opex"] += amount

    rows_by_month = [monthly[key] for key in sorted(monthly.keys())][-12:]
    max_value = max(
        [max(row["revenue"], row["cogs"], row["opex"]) for row in rows_by_month] or [Decimal("0")]
    )
    for row in rows_by_month:
        row["net"] = row["revenue"] - row["cogs"] - row["opex"]
        row["revenue_bar"] = int((row["revenue"] / max_value) * 100) if max_value > 0 else 0
        row["cost_bar"] = int(((row["cogs"] + row["opex"]) / max_value) * 100) if max_value > 0 else 0
    return rows_by_month


def _pl_side_comparison(rows):
    sides = {
        "CA": {"side": "CA", "label": "Canada", "revenue": Decimal("0"), "cogs": Decimal("0"), "opex": Decimal("0")},
        "BD": {"side": "BD", "label": "Bangladesh", "revenue": Decimal("0"), "cogs": Decimal("0"), "opex": Decimal("0")},
    }
    for row in rows:
        side = row["side"] if row["side"] in sides else "CA"
        entry = row["entry"]
        main_type = row["main_type"]
        direction = (entry.direction or "").upper().strip()
        amount = row["amount_cad"]
        if direction == AccountingEntry.DIR_IN and main_type == "INCOME":
            sides[side]["revenue"] += amount
        elif direction == AccountingEntry.DIR_OUT and main_type == "COGS":
            sides[side]["cogs"] += amount
        elif direction == AccountingEntry.DIR_OUT and main_type in PL_OPEX_TYPES:
            sides[side]["opex"] += amount

    result = []
    for row in sides.values():
        row["gross_profit"] = row["revenue"] - row["cogs"]
        row["net_profit"] = row["gross_profit"] - row["opex"]
        result.append(row)
    return result


@login_required
def profit_loss_dashboard(request):
    filters = {
        "date_from": _parse_pl_date(request.GET.get("date_from")),
        "date_to": _parse_pl_date(request.GET.get("date_to")),
        "customer_id": (request.GET.get("customer") or "").strip(),
        "product_category": (request.GET.get("product_category") or "").strip(),
        "side": (request.GET.get("side") or "").strip().upper(),
        "currency": (request.GET.get("currency") or "").strip().upper(),
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "customer": filters["customer_id"],
        "product_category": filters["product_category"],
        "side": filters["side"],
        "currency": filters["currency"],
    }

    qs = (
        AccountingEntry.objects.exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .select_related("customer", "opportunity", "production_order", "production_order__customer", "production_order__opportunity", "production_order__product")
    )
    if filters["date_from"]:
        qs = qs.filter(date__gte=filters["date_from"])
    if filters["date_to"]:
        qs = qs.filter(date__lte=filters["date_to"])
    if filters["customer_id"]:
        qs = qs.filter(Q(customer_id=filters["customer_id"]) | Q(production_order__customer_id=filters["customer_id"]))
    if filters["side"]:
        qs = qs.filter(side=filters["side"])
    if filters["currency"]:
        qs = qs.filter(currency=filters["currency"])

    rows = [_pl_row(entry) for entry in qs.order_by("date", "id")[:1500]]
    if filters["product_category"]:
        rows = [row for row in rows if row["product_category"] == filters["product_category"]]

    revenue_rows = [
        row for row in rows
        if (row["entry"].direction or "").upper().strip() == AccountingEntry.DIR_IN and row["main_type"] == "INCOME"
    ]
    cogs_rows = [
        row for row in rows
        if (row["entry"].direction or "").upper().strip() == AccountingEntry.DIR_OUT and row["main_type"] == "COGS"
    ]
    opex_rows = [
        row for row in rows
        if (row["entry"].direction or "").upper().strip() == AccountingEntry.DIR_OUT and row["main_type"] in PL_OPEX_TYPES
    ]

    total_revenue = sum((row["amount_cad"] for row in revenue_rows), Decimal("0"))
    total_cogs = sum((row["amount_cad"] for row in cogs_rows), Decimal("0"))
    operating_expenses = sum((row["amount_cad"] for row in opex_rows), Decimal("0"))
    gross_profit = total_revenue - total_cogs
    net_profit = gross_profit - operating_expenses
    gross_margin_percent = (gross_profit / total_revenue * Decimal("100")).quantize(Decimal("0.01")) if total_revenue > 0 else Decimal("0")
    net_margin_percent = (net_profit / total_revenue * Decimal("100")).quantize(Decimal("0.01")) if total_revenue > 0 else Decimal("0")

    customers = Customer.objects.filter(accounting_entries__isnull=False).distinct().order_by("account_brand", "contact_name")
    product_categories = sorted({
        value.strip() for value in Product.objects.exclude(product_category="").values_list("product_category", flat=True).distinct()
        if value and value.strip()
    } | {
        row["product_category"] for row in rows if row["product_category"] and row["product_category"] != "Uncategorized"
    })

    return render(
        request,
        "crm/accounting_profit_loss_dashboard.html",
        {
            "filter_values": filter_values,
            "customers": customers,
            "product_categories": product_categories,
            "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
            "currency_options": ["CAD", "BDT", "USD"],
            "total_revenue": total_revenue,
            "total_cogs": total_cogs,
            "gross_profit": gross_profit,
            "gross_margin_percent": gross_margin_percent,
            "operating_expenses": operating_expenses,
            "net_profit": net_profit,
            "net_margin_percent": net_margin_percent,
            "revenue_currency_totals": _pl_currency_totals(revenue_rows),
            "cogs_currency_totals": _pl_currency_totals(cogs_rows),
            "opex_currency_totals": _pl_currency_totals(opex_rows),
            "revenue_by_customer": _pl_group(revenue_rows, "customer"),
            "revenue_by_product_category": _pl_group(revenue_rows, "product_category"),
            "cost_by_category": _pl_group(cogs_rows, "cost_category"),
            "opex_by_category": _pl_group(opex_rows, "cost_category"),
            "monthly_rows": _pl_monthly_rows(rows),
            "side_rows": _pl_side_comparison(rows),
            "entry_count": len(rows),
            "revenue_count": len(revenue_rows),
            "cogs_count": len(cogs_rows),
            "opex_count": len(opex_rows),
        },
    )


def _exec_payment_amount_cad(payment):
    amount_cad = _pl_decimal(getattr(payment, "amount_cad", None))
    if amount_cad:
        return amount_cad
    if (payment.currency or "").upper().strip() == "CAD":
        return _pl_decimal(payment.amount)
    return Decimal("0")


def _exec_invoice_side(invoice):
    region = (getattr(invoice, "invoice_region", "") or "").upper().strip()
    if region in {"CA", "BD"}:
        return region
    currency = (invoice.currency or "").upper().strip()
    return "BD" if currency == "BDT" else "CA"


def _exec_customer_label(customer):
    if not customer:
        return "No customer"
    return customer.account_brand or customer.contact_name or f"Customer {customer.pk}"


def _exec_currency_totals(rows, currency_getter, amount_getter):
    totals = {}
    for row in rows:
        currency = (currency_getter(row) or "Unknown").upper()
        totals[currency] = totals.get(currency, Decimal("0")) + _pl_decimal(amount_getter(row))
    return [
        {"currency": currency, "amount": amount}
        for currency, amount in sorted(totals.items())
        if amount != 0
    ]


def _exec_monthly_cash_rows(entries):
    monthly = {}
    for entry in entries:
        if not entry.date:
            continue
        key = entry.date.strftime("%Y-%m")
        if key not in monthly:
            monthly[key] = {
                "key": key,
                "label": entry.date.strftime("%b %Y"),
                "cash_in": Decimal("0"),
                "cash_out": Decimal("0"),
                "net": Decimal("0"),
            }
        amount = _pl_amount_cad(entry)
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN:
            monthly[key]["cash_in"] += amount
        elif (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT:
            monthly[key]["cash_out"] += amount

    rows = [monthly[key] for key in sorted(monthly.keys())][-12:]
    max_value = max(
        [max(row["cash_in"], row["cash_out"]) for row in rows] or [Decimal("0")]
    )
    for row in rows:
        row["net"] = row["cash_in"] - row["cash_out"]
        row["in_bar"] = int((row["cash_in"] / max_value) * 100) if max_value > 0 else 0
        row["out_bar"] = int((row["cash_out"] / max_value) * 100) if max_value > 0 else 0
    return rows


def _exec_health_score(total_revenue, total_received, total_receivables, total_payables, net_profit, cash_flow, overdue_count, due_vendor_count):
    score = Decimal("65")
    if total_revenue > 0:
        score += Decimal("8")
    if total_received > 0:
        score += Decimal("7")
    score += Decimal("10") if net_profit >= 0 else Decimal("-12")
    score += Decimal("8") if cash_flow >= 0 else Decimal("-10")
    if total_revenue > 0 and total_receivables > total_revenue * Decimal("0.35"):
        score -= Decimal("8")
    if total_received > 0 and total_payables > total_received * Decimal("0.50"):
        score -= Decimal("7")
    if overdue_count:
        score -= min(Decimal(overdue_count) * Decimal("2"), Decimal("12"))
    if due_vendor_count:
        score -= min(Decimal(due_vendor_count), Decimal("8"))
    score = max(Decimal("0"), min(Decimal("100"), score))
    if score >= 82:
        label = "Strong"
        tone = "good"
    elif score >= 65:
        label = "Stable"
        tone = "blue"
    elif score >= 45:
        label = "Needs attention"
        tone = "warn"
    else:
        label = "High risk"
        tone = "bad"
    return int(score), label, tone


@login_required
def executive_financial_dashboard(request):
    today = timezone.localdate()
    filters = {
        "date_from": _parse_pl_date(request.GET.get("date_from")),
        "date_to": _parse_pl_date(request.GET.get("date_to")),
        "currency": (request.GET.get("currency") or "").strip().upper(),
        "side": (request.GET.get("side") or "").strip().upper(),
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "currency": filters["currency"],
        "side": filters["side"],
    }

    accounting_qs = (
        AccountingEntry.objects.exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .select_related("customer", "opportunity", "production_order", "production_order__customer", "production_order__opportunity", "production_order__product")
    )
    if filters["date_from"]:
        accounting_qs = accounting_qs.filter(date__gte=filters["date_from"])
    if filters["date_to"]:
        accounting_qs = accounting_qs.filter(date__lte=filters["date_to"])
    if filters["side"]:
        accounting_qs = accounting_qs.filter(side=filters["side"])
    if filters["currency"]:
        accounting_qs = accounting_qs.filter(currency=filters["currency"])

    entries = list(accounting_qs.order_by("date", "id")[:2000])
    pl_rows = [_pl_row(entry) for entry in entries]
    revenue_rows = [
        row for row in pl_rows
        if (row["entry"].direction or "").upper().strip() == AccountingEntry.DIR_IN and row["main_type"] == "INCOME"
    ]
    cogs_rows = [
        row for row in pl_rows
        if (row["entry"].direction or "").upper().strip() == AccountingEntry.DIR_OUT and row["main_type"] == "COGS"
    ]
    opex_rows = [
        row for row in pl_rows
        if (row["entry"].direction or "").upper().strip() == AccountingEntry.DIR_OUT and row["main_type"] in PL_OPEX_TYPES
    ]

    total_revenue = sum((row["amount_cad"] for row in revenue_rows), Decimal("0"))
    total_cogs = sum((row["amount_cad"] for row in cogs_rows), Decimal("0"))
    total_opex = sum((row["amount_cad"] for row in opex_rows), Decimal("0"))
    net_profit = total_revenue - total_cogs - total_opex
    cash_in = sum(
        (_pl_amount_cad(entry) for entry in entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN),
        Decimal("0"),
    )
    cash_out = sum(
        (_pl_amount_cad(entry) for entry in entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT),
        Decimal("0"),
    )
    cash_flow = cash_in - cash_out

    invoice_qs = Invoice.objects.exclude(status="cancelled").select_related("customer", "order", "order__customer")
    if filters["date_from"]:
        invoice_qs = invoice_qs.filter(issue_date__gte=filters["date_from"])
    if filters["date_to"]:
        invoice_qs = invoice_qs.filter(issue_date__lte=filters["date_to"])
    if filters["currency"]:
        invoice_qs = invoice_qs.filter(currency=filters["currency"])
    invoices = list(invoice_qs.order_by("due_date", "-issue_date", "-created_at")[:1500])
    if filters["side"]:
        invoices = [invoice for invoice in invoices if _exec_invoice_side(invoice) == filters["side"]]

    payment_qs = InvoicePayment.objects.select_related("invoice", "invoice__customer", "production_order", "accounting_entry")
    if filters["date_from"]:
        payment_qs = payment_qs.filter(payment_date__gte=filters["date_from"])
    if filters["date_to"]:
        payment_qs = payment_qs.filter(payment_date__lte=filters["date_to"])
    if filters["currency"]:
        payment_qs = payment_qs.filter(currency=filters["currency"])
    if filters["side"]:
        payment_qs = payment_qs.filter(side=filters["side"])
    payments = list(payment_qs.order_by("-payment_date", "-id")[:1500])

    open_invoices = [invoice for invoice in invoices if _pl_decimal(invoice.balance) > 0]
    overdue_invoices = [invoice for invoice in open_invoices if invoice.due_date and invoice.due_date < today]
    total_receivables = sum((_pl_decimal(invoice.balance) for invoice in open_invoices), Decimal("0"))
    total_received = sum((_exec_payment_amount_cad(payment) for payment in payments), Decimal("0"))

    payable_rows = [
        _ap_row(entry, today)
        for entry in entries
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT
    ]
    open_payable_rows = [row for row in payable_rows if row["status_key"] != "paid"]
    due_vendor_rows = [
        row for row in open_payable_rows
        if row["entry"].date and row["entry"].date <= today + timedelta(days=7)
    ]
    total_payables = sum((_pl_amount_cad(row["entry"]) for row in open_payable_rows), Decimal("0"))

    customer_map = {}
    for row in revenue_rows:
        label = row["customer"]
        if label not in customer_map:
            customer_map[label] = {"label": label, "revenue": Decimal("0"), "receivable": Decimal("0"), "count": 0}
        customer_map[label]["revenue"] += row["amount_cad"]
        customer_map[label]["count"] += 1
    for invoice in open_invoices:
        label = _exec_customer_label(invoice.customer or getattr(invoice.order, "customer", None))
        if label not in customer_map:
            customer_map[label] = {"label": label, "revenue": Decimal("0"), "receivable": Decimal("0"), "count": 0}
        customer_map[label]["receivable"] += _pl_decimal(invoice.balance)
    top_customer_rows = sorted(
        customer_map.values(),
        key=lambda row: (row["revenue"], row["receivable"]),
        reverse=True,
    )[:12]

    supplier_map = {}
    for row in open_payable_rows:
        supplier = row["supplier"]
        if supplier not in supplier_map:
            supplier_map[supplier] = {"label": supplier, "payable": Decimal("0"), "count": 0, "overdue_count": 0}
        supplier_map[supplier]["payable"] += _pl_amount_cad(row["entry"])
        supplier_map[supplier]["count"] += 1
        supplier_map[supplier]["overdue_count"] += 1 if row["status_key"] == "overdue" else 0
    top_supplier_rows = sorted(supplier_map.values(), key=lambda row: row["payable"], reverse=True)[:12]

    side_rows = []
    for side, label in [("CA", "Canada"), ("BD", "Bangladesh")]:
        side_entries = [entry for entry in entries if (entry.side or "").upper().strip() == side]
        side_revenue = sum(
            (_pl_amount_cad(entry) for entry in side_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN and (entry.main_type or "").upper().strip() == "INCOME"),
            Decimal("0"),
        )
        side_cogs = sum(
            (_pl_amount_cad(entry) for entry in side_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT and (entry.main_type or "").upper().strip() == "COGS"),
            Decimal("0"),
        )
        side_opex = sum(
            (_pl_amount_cad(entry) for entry in side_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT and (entry.main_type or "").upper().strip() in PL_OPEX_TYPES),
            Decimal("0"),
        )
        side_payables = sum(
            (_pl_amount_cad(row["entry"]) for row in open_payable_rows if (row["entry"].side or "").upper().strip() == side),
            Decimal("0"),
        )
        side_received = sum((_exec_payment_amount_cad(payment) for payment in payments if payment.side == side), Decimal("0"))
        side_receivables = sum((_pl_decimal(invoice.balance) for invoice in open_invoices if _exec_invoice_side(invoice) == side), Decimal("0"))
        side_rows.append(
            {
                "side": side,
                "label": label,
                "revenue": side_revenue,
                "received": side_received,
                "receivables": side_receivables,
                "payables": side_payables,
                "net_profit": side_revenue - side_cogs - side_opex,
            }
        )

    health_score, health_label, health_tone = _exec_health_score(
        total_revenue,
        total_received,
        total_receivables,
        total_payables,
        net_profit,
        cash_flow,
        len(overdue_invoices),
        len(due_vendor_rows),
    )

    return render(
        request,
        "crm/accounting_executive_dashboard.html",
        {
            "filter_values": filter_values,
            "currency_options": ["CAD", "BDT", "USD"],
            "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
            "total_revenue": total_revenue,
            "total_received": total_received,
            "total_receivables": total_receivables,
            "total_payables": total_payables,
            "net_profit": net_profit,
            "cash_flow": cash_flow,
            "cash_in": cash_in,
            "cash_out": cash_out,
            "overdue_invoices": len(overdue_invoices),
            "due_vendor_bills": len(due_vendor_rows),
            "receivable_currency_totals": _exec_currency_totals(open_invoices, lambda invoice: invoice.currency, lambda invoice: invoice.balance),
            "received_currency_totals": _exec_currency_totals(payments, lambda payment: payment.currency, lambda payment: payment.amount),
            "payable_currency_totals": _exec_currency_totals(open_payable_rows, lambda row: row["entry"].currency, lambda row: row["amount"]),
            "top_customer_rows": top_customer_rows,
            "top_supplier_rows": top_supplier_rows,
            "monthly_rows": _exec_monthly_cash_rows(entries),
            "side_rows": side_rows,
            "health_score": health_score,
            "health_label": health_label,
            "health_tone": health_tone,
            "entry_count": len(entries),
            "invoice_count": len(invoices),
            "payment_count": len(payments),
        },
    )


BS_CURRENT_ASSET_KEYWORDS = ("prepaid", "advance", "deposit", "retainer")
BS_FIXED_ASSET_KEYWORDS = ("fixed asset", "equipment", "machine", "machinery", "computer", "furniture", "vehicle")
BS_CREDIT_CARD_KEYWORDS = ("credit card", "visa", "mastercard", "amex", "card payable")
BS_LOAN_KEYWORDS = ("loan", "financing", "borrow", "lender")
BS_TAX_KEYWORDS = ("tax", "hst", "gst", "vat", "source deduction")
BS_EQUITY_KEYWORDS = ("owner", "capital", "equity", "shareholder", "investment")


def _bs_entry_text(entry):
    return " ".join([
        entry.main_type or "",
        entry.sub_type or "",
        entry.description or "",
        entry.internal_note or "",
    ]).lower()


def _bs_has(text, keywords):
    return any(keyword in text for keyword in keywords)


def _bs_latest_cad_to_bdt():
    row = ExchangeRate.objects.order_by("-updated_at").first()
    return row.cad_to_bdt if row and row.cad_to_bdt else Decimal("0")


def _bs_invoice_balance_cad(invoice, cad_to_bdt):
    balance = _pl_decimal(invoice.balance)
    currency = (invoice.currency or "").upper().strip()
    if currency == "CAD":
        return balance
    if currency == "BDT" and cad_to_bdt > 0:
        return (balance / cad_to_bdt).quantize(Decimal("0.01"))
    return Decimal("0")


def _bs_inventory_value_cad(filters):
    if filters["side"] or filters["currency"] not in {"", "CAD"}:
        return Decimal("0")
    total = Decimal("0")
    for item in InventoryItem.objects.filter(is_active=True).only("unit_cost", "quantity"):
        total += _pl_decimal(item.unit_cost) * _pl_decimal(item.quantity)
    return total.quantize(Decimal("0.01"))


def _bs_profit_from_entries(entries):
    revenue = Decimal("0")
    cogs = Decimal("0")
    opex = Decimal("0")
    for entry in entries:
        text_type = (entry.main_type or "").upper().strip()
        direction = (entry.direction or "").upper().strip()
        amount = _pl_amount_cad(entry)
        if direction == AccountingEntry.DIR_IN and text_type == "INCOME":
            revenue += amount
        elif direction == AccountingEntry.DIR_OUT and text_type == "COGS":
            cogs += amount
        elif direction == AccountingEntry.DIR_OUT and text_type in PL_OPEX_TYPES:
            opex += amount
    return revenue - cogs - opex


def _bs_owner_capital(entries):
    total = Decimal("0")
    for entry in entries:
        text = _bs_entry_text(entry)
        if not _bs_has(text, BS_EQUITY_KEYWORDS):
            continue
        amount = _pl_amount_cad(entry)
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN:
            total += amount
        elif (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT:
            total -= amount
    return total


def _bs_liability_bucket(entry):
    text = _bs_entry_text(entry)
    if _bs_has(text, BS_CREDIT_CARD_KEYWORDS):
        return "credit_cards"
    if _bs_has(text, BS_LOAN_KEYWORDS):
        return "loans"
    if _bs_has(text, BS_TAX_KEYWORDS):
        return "taxes_payable"
    return "accounts_payable"


def _bs_monthly_rows(entries):
    monthly = {}
    for entry in entries:
        if not entry.date:
            continue
        key = entry.date.strftime("%Y-%m")
        if key not in monthly:
            monthly[key] = {
                "key": key,
                "label": entry.date.strftime("%b %Y"),
                "assets": Decimal("0"),
                "liabilities": Decimal("0"),
                "equity": Decimal("0"),
            }
        amount = _pl_amount_cad(entry)
        direction = (entry.direction or "").upper().strip()
        main_type = (entry.main_type or "").upper().strip()
        text = _bs_entry_text(entry)
        if direction == AccountingEntry.DIR_IN:
            monthly[key]["assets"] += amount
            if _bs_has(text, BS_LOAN_KEYWORDS):
                monthly[key]["liabilities"] += amount
            elif main_type == "INCOME" or _bs_has(text, BS_EQUITY_KEYWORDS):
                monthly[key]["equity"] += amount
        elif direction == AccountingEntry.DIR_OUT:
            monthly[key]["assets"] -= amount
            if _bs_has(text, BS_CURRENT_ASSET_KEYWORDS) or _bs_has(text, BS_FIXED_ASSET_KEYWORDS):
                monthly[key]["assets"] += amount
            if main_type in {"COGS", "EXPENSE", "TAX", "OTHER"}:
                monthly[key]["equity"] -= amount
    rows = [monthly[key] for key in sorted(monthly.keys())][-12:]
    max_value = max(
        [max(abs(row["assets"]), abs(row["liabilities"]), abs(row["equity"])) for row in rows] or [Decimal("0")]
    )
    for row in rows:
        row["asset_bar"] = int((abs(row["assets"]) / max_value) * 100) if max_value > 0 else 0
        row["liability_bar"] = int((abs(row["liabilities"]) / max_value) * 100) if max_value > 0 else 0
        row["equity_bar"] = int((abs(row["equity"]) / max_value) * 100) if max_value > 0 else 0
    return rows


@login_required
def balance_sheet_dashboard(request):
    today = timezone.localdate()
    filters = {
        "date_from": _parse_pl_date(request.GET.get("date_from")),
        "date_to": _parse_pl_date(request.GET.get("date_to")),
        "currency": (request.GET.get("currency") or "").strip().upper(),
        "side": (request.GET.get("side") or "").strip().upper(),
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "currency": filters["currency"],
        "side": filters["side"],
    }

    accounting_qs = (
        AccountingEntry.objects.exclude(status__iexact="CANCELLED")
        .select_related("customer", "opportunity", "production_order", "production_order__customer", "production_order__opportunity", "production_order__product")
    )
    if filters["date_from"]:
        accounting_qs = accounting_qs.filter(date__gte=filters["date_from"])
    if filters["date_to"]:
        accounting_qs = accounting_qs.filter(date__lte=filters["date_to"])
    if filters["side"]:
        accounting_qs = accounting_qs.filter(side=filters["side"])
    if filters["currency"]:
        accounting_qs = accounting_qs.filter(currency=filters["currency"])

    entries = list(accounting_qs.order_by("date", "id")[:2500])
    non_transfer_entries = [entry for entry in entries if (entry.main_type or "").upper().strip() != "TRANSFER"]
    cad_to_bdt = _bs_latest_cad_to_bdt()

    cash_and_bank = sum(
        (
            _pl_amount_cad(entry) if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN else -_pl_amount_cad(entry)
            for entry in entries
        ),
        Decimal("0"),
    )
    prepaid_expenses = sum(
        (
            _pl_amount_cad(entry)
            for entry in non_transfer_entries
            if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT and _bs_has(_bs_entry_text(entry), BS_CURRENT_ASSET_KEYWORDS)
        ),
        Decimal("0"),
    )
    fixed_assets = sum(
        (
            _pl_amount_cad(entry)
            for entry in non_transfer_entries
            if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT and _bs_has(_bs_entry_text(entry), BS_FIXED_ASSET_KEYWORDS)
        ),
        Decimal("0"),
    )
    inventory_value = _bs_inventory_value_cad(filters)

    invoice_qs = Invoice.objects.exclude(status="cancelled").select_related("customer", "order", "order__customer")
    if filters["date_from"]:
        invoice_qs = invoice_qs.filter(issue_date__gte=filters["date_from"])
    if filters["date_to"]:
        invoice_qs = invoice_qs.filter(issue_date__lte=filters["date_to"])
    if filters["currency"]:
        invoice_qs = invoice_qs.filter(currency=filters["currency"])
    invoices = list(invoice_qs.order_by("due_date", "-issue_date", "-created_at")[:2000])
    if filters["side"]:
        invoices = [invoice for invoice in invoices if _exec_invoice_side(invoice) == filters["side"]]
    open_invoices = [invoice for invoice in invoices if _pl_decimal(invoice.balance) > 0]
    accounts_receivable = sum((_bs_invoice_balance_cad(invoice, cad_to_bdt) for invoice in open_invoices), Decimal("0"))

    payable_rows = [
        _ap_row(entry, today)
        for entry in non_transfer_entries
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT
    ]
    open_payable_rows = [row for row in payable_rows if row["status_key"] != "paid"]
    liability_buckets = {
        "accounts_payable": Decimal("0"),
        "credit_cards": Decimal("0"),
        "loans": Decimal("0"),
        "taxes_payable": Decimal("0"),
    }
    for row in open_payable_rows:
        liability_buckets[_bs_liability_bucket(row["entry"])] += _pl_amount_cad(row["entry"])
    for entry in non_transfer_entries:
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN and _bs_has(_bs_entry_text(entry), BS_LOAN_KEYWORDS):
            liability_buckets["loans"] += _pl_amount_cad(entry)

    current_assets = cash_and_bank + accounts_receivable + inventory_value + prepaid_expenses
    total_assets = current_assets + fixed_assets
    total_liabilities = sum(liability_buckets.values(), Decimal("0"))

    retained_entries = []
    if filters["date_from"]:
        retained_qs = AccountingEntry.objects.exclude(main_type="TRANSFER").exclude(status__iexact="CANCELLED").filter(date__lt=filters["date_from"])
        if filters["side"]:
            retained_qs = retained_qs.filter(side=filters["side"])
        if filters["currency"]:
            retained_qs = retained_qs.filter(currency=filters["currency"])
        retained_entries = list(retained_qs.order_by("date", "id")[:2500])

    owner_capital = _bs_owner_capital(non_transfer_entries)
    retained_earnings = _bs_profit_from_entries(retained_entries)
    current_period_profit = _bs_profit_from_entries(non_transfer_entries)
    total_equity = owner_capital + retained_earnings + current_period_profit
    equation_right = total_liabilities + total_equity
    balance_difference = total_assets - equation_right
    is_balanced = abs(balance_difference) <= Decimal("0.01")

    side_rows = []
    for side, label in [("CA", "Canada"), ("BD", "Bangladesh")]:
        side_entries = [entry for entry in entries if (entry.side or "").upper().strip() == side]
        side_non_transfer = [entry for entry in side_entries if (entry.main_type or "").upper().strip() != "TRANSFER"]
        side_cash = sum(
            (
                _pl_amount_cad(entry) if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN else -_pl_amount_cad(entry)
                for entry in side_entries
            ),
            Decimal("0"),
        )
        side_ar = sum((_bs_invoice_balance_cad(invoice, cad_to_bdt) for invoice in open_invoices if _exec_invoice_side(invoice) == side), Decimal("0"))
        side_payables = sum(
            (
                _pl_amount_cad(row["entry"])
                for row in open_payable_rows
                if (row["entry"].side or "").upper().strip() == side
            ),
            Decimal("0"),
        )
        side_profit = _bs_profit_from_entries(side_non_transfer)
        side_rows.append(
            {
                "side": side,
                "label": label,
                "assets": side_cash + side_ar,
                "liabilities": side_payables,
                "equity": side_profit,
                "difference": side_cash + side_ar - side_payables - side_profit,
            }
        )

    return render(
        request,
        "crm/accounting_balance_sheet_dashboard.html",
        {
            "filter_values": filter_values,
            "currency_options": ["CAD", "BDT", "USD"],
            "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
            "total_assets": total_assets,
            "current_assets": current_assets,
            "cash_and_bank": cash_and_bank,
            "accounts_receivable": accounts_receivable,
            "inventory_value": inventory_value,
            "prepaid_expenses": prepaid_expenses,
            "fixed_assets": fixed_assets,
            "total_liabilities": total_liabilities,
            "accounts_payable": liability_buckets["accounts_payable"],
            "credit_cards": liability_buckets["credit_cards"],
            "loans": liability_buckets["loans"],
            "taxes_payable": liability_buckets["taxes_payable"],
            "total_equity": total_equity,
            "owner_capital": owner_capital,
            "retained_earnings": retained_earnings,
            "current_period_profit": current_period_profit,
            "equation_right": equation_right,
            "balance_difference": balance_difference,
            "is_balanced": is_balanced,
            "balance_status": "Balanced" if is_balanced else "Out of Balance",
            "side_rows": side_rows,
            "monthly_rows": _bs_monthly_rows(entries),
            "entry_count": len(entries),
            "invoice_count": len(invoices),
            "open_invoice_count": len(open_invoices),
            "open_payable_count": len(open_payable_rows),
        },
    )


def _cf_signed_amount(entry):
    amount = _pl_amount_cad(entry)
    return amount if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN else -amount


def _cf_is_tax_payment(entry):
    return (entry.main_type or "").upper().strip() == "TAX" or _bs_has(_bs_entry_text(entry), BS_TAX_KEYWORDS)


def _cf_is_loan_payment(entry):
    return _bs_has(_bs_entry_text(entry), BS_LOAN_KEYWORDS)


def _cf_group_cash(entries, label_getter, limit=12):
    grouped = {}
    for entry in entries:
        label = label_getter(entry) or "Unassigned"
        if label not in grouped:
            grouped[label] = {"label": label, "amount": Decimal("0"), "count": 0}
        grouped[label]["amount"] += _pl_amount_cad(entry)
        grouped[label]["count"] += 1
    return sorted(grouped.values(), key=lambda row: row["amount"], reverse=True)[:limit]


def _cf_daily_rows(entries):
    grouped = {}
    for entry in entries:
        if not entry.date:
            continue
        key = entry.date.isoformat()
        if key not in grouped:
            grouped[key] = {"key": key, "label": entry.date.strftime("%b %d"), "cash_in": Decimal("0"), "cash_out": Decimal("0"), "net": Decimal("0")}
        amount = _pl_amount_cad(entry)
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN:
            grouped[key]["cash_in"] += amount
        else:
            grouped[key]["cash_out"] += amount
    rows = [grouped[key] for key in sorted(grouped.keys())][-21:]
    for row in rows:
        row["net"] = row["cash_in"] - row["cash_out"]
    return rows


def _cf_weekly_rows(entries):
    grouped = {}
    for entry in entries:
        if not entry.date:
            continue
        iso = entry.date.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        if key not in grouped:
            grouped[key] = {"key": key, "label": f"W{iso.week:02d} {iso.year}", "cash_in": Decimal("0"), "cash_out": Decimal("0"), "net": Decimal("0")}
        amount = _pl_amount_cad(entry)
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN:
            grouped[key]["cash_in"] += amount
        else:
            grouped[key]["cash_out"] += amount
    rows = [grouped[key] for key in sorted(grouped.keys())][-12:]
    for row in rows:
        row["net"] = row["cash_in"] - row["cash_out"]
    return rows


def _cf_monthly_rows(entries):
    grouped = {}
    for entry in entries:
        if not entry.date:
            continue
        key = entry.date.strftime("%Y-%m")
        if key not in grouped:
            grouped[key] = {"key": key, "label": entry.date.strftime("%b %Y"), "cash_in": Decimal("0"), "cash_out": Decimal("0"), "net": Decimal("0")}
        amount = _pl_amount_cad(entry)
        if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN:
            grouped[key]["cash_in"] += amount
        else:
            grouped[key]["cash_out"] += amount
    rows = [grouped[key] for key in sorted(grouped.keys())][-12:]
    max_value = max([max(row["cash_in"], row["cash_out"]) for row in rows] or [Decimal("0")])
    for row in rows:
        row["net"] = row["cash_in"] - row["cash_out"]
        row["in_bar"] = int((row["cash_in"] / max_value) * 100) if max_value > 0 else 0
        row["out_bar"] = int((row["cash_out"] / max_value) * 100) if max_value > 0 else 0
    return rows


@login_required
def cash_flow_dashboard(request):
    today = timezone.localdate()
    forecast_end = today + timedelta(days=30)
    filters = {
        "date_from": _parse_pl_date(request.GET.get("date_from")),
        "date_to": _parse_pl_date(request.GET.get("date_to")),
        "currency": (request.GET.get("currency") or "").strip().upper(),
        "side": (request.GET.get("side") or "").strip().upper(),
        "customer_id": (request.GET.get("customer") or "").strip(),
        "supplier": (request.GET.get("supplier") or "").strip(),
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "currency": filters["currency"],
        "side": filters["side"],
        "customer": filters["customer_id"],
        "supplier": filters["supplier"],
    }

    base_qs = (
        AccountingEntry.objects.exclude(status__iexact="CANCELLED")
        .select_related("customer", "opportunity", "production_order", "production_order__customer", "shipment")
    )
    if filters["side"]:
        base_qs = base_qs.filter(side=filters["side"])
    if filters["currency"]:
        base_qs = base_qs.filter(currency=filters["currency"])
    if filters["customer_id"]:
        base_qs = base_qs.filter(Q(customer_id=filters["customer_id"]) | Q(production_order__customer_id=filters["customer_id"]))

    supplier_source = list(base_qs.filter(direction=AccountingEntry.DIR_OUT).order_by("-date", "-id")[:1500])
    supplier_choices = sorted({_ap_supplier_label(entry) for entry in supplier_source})

    opening_entries = []
    if filters["date_from"]:
        opening_entries = list(base_qs.filter(date__lt=filters["date_from"]).order_by("date", "id")[:2500])

    period_qs = base_qs
    if filters["date_from"]:
        period_qs = period_qs.filter(date__gte=filters["date_from"])
    if filters["date_to"]:
        period_qs = period_qs.filter(date__lte=filters["date_to"])
    period_entries = list(period_qs.order_by("date", "id")[:2500])

    if filters["supplier"]:
        opening_entries = [entry for entry in opening_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT and _ap_supplier_label(entry) == filters["supplier"]]
        period_entries = [entry for entry in period_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT and _ap_supplier_label(entry) == filters["supplier"]]

    inflow_entries = [entry for entry in period_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN]
    outflow_entries = [entry for entry in period_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT]

    opening_cash_balance = sum((_cf_signed_amount(entry) for entry in opening_entries), Decimal("0"))
    cash_received_from_customers = sum(
        (
            _pl_amount_cad(entry)
            for entry in inflow_entries
            if (entry.main_type or "").upper().strip() == "INCOME" or entry.customer_id or (entry.production_order_id and entry.production_order and entry.production_order.customer_id)
        ),
        Decimal("0"),
    )
    cash_paid_to_suppliers = sum(
        (_pl_amount_cad(entry) for entry in outflow_entries if (entry.main_type or "").upper().strip() == "COGS"),
        Decimal("0"),
    )
    loan_payments = sum((_pl_amount_cad(entry) for entry in outflow_entries if _cf_is_loan_payment(entry)), Decimal("0"))
    tax_payments = sum((_pl_amount_cad(entry) for entry in outflow_entries if _cf_is_tax_payment(entry)), Decimal("0"))
    operating_expenses_paid = sum(
        (
            _pl_amount_cad(entry)
            for entry in outflow_entries
            if (entry.main_type or "").upper().strip() in {"EXPENSE", "OTHER"} and not _cf_is_loan_payment(entry) and not _cf_is_tax_payment(entry)
        ),
        Decimal("0"),
    )
    net_cash_flow = sum((_cf_signed_amount(entry) for entry in period_entries), Decimal("0"))
    closing_cash_balance = opening_cash_balance + net_cash_flow

    invoice_qs = Invoice.objects.exclude(status="cancelled").select_related("customer", "order", "order__customer").filter(due_date__gte=today, due_date__lte=forecast_end)
    if filters["currency"]:
        invoice_qs = invoice_qs.filter(currency=filters["currency"])
    if filters["customer_id"]:
        invoice_qs = invoice_qs.filter(Q(customer_id=filters["customer_id"]) | Q(order__customer_id=filters["customer_id"]))
    forecast_invoices = list(invoice_qs.order_by("due_date", "-issue_date")[:500])
    if filters["side"]:
        forecast_invoices = [invoice for invoice in forecast_invoices if _exec_invoice_side(invoice) == filters["side"]]
    cad_to_bdt = _bs_latest_cad_to_bdt()
    forecast_receivables = sum((_bs_invoice_balance_cad(invoice, cad_to_bdt) for invoice in forecast_invoices if _pl_decimal(invoice.balance) > 0), Decimal("0"))

    forecast_payable_qs = base_qs.filter(direction=AccountingEntry.DIR_OUT, date__gte=today, date__lte=forecast_end)
    forecast_payable_entries = list(forecast_payable_qs.order_by("date", "id")[:500])
    forecast_payable_rows = [_ap_row(entry, today) for entry in forecast_payable_entries]
    forecast_payable_rows = [row for row in forecast_payable_rows if row["status_key"] != "paid"]
    if filters["supplier"]:
        forecast_payable_rows = [row for row in forecast_payable_rows if _ap_supplier_label(row["entry"]) == filters["supplier"]]
    forecast_payables = sum((_pl_amount_cad(row["entry"]) for row in forecast_payable_rows), Decimal("0"))
    forecast_net = forecast_receivables - forecast_payables
    forecast_closing_cash = closing_cash_balance + forecast_net
    if forecast_closing_cash < 0:
        low_cash_label = "Low cash risk"
        low_cash_tone = "bad"
    elif forecast_closing_cash < max(forecast_payables, Decimal("0.01")) * Decimal("0.25"):
        low_cash_label = "Watch cash"
        low_cash_tone = "warn"
    else:
        low_cash_label = "Cash stable"
        low_cash_tone = "good"

    side_rows = []
    for side, label in [("CA", "Canada"), ("BD", "Bangladesh")]:
        side_entries = [entry for entry in period_entries if (entry.side or "").upper().strip() == side]
        side_in = sum((_pl_amount_cad(entry) for entry in side_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_IN), Decimal("0"))
        side_out = sum((_pl_amount_cad(entry) for entry in side_entries if (entry.direction or "").upper().strip() == AccountingEntry.DIR_OUT), Decimal("0"))
        side_rows.append({"side": side, "label": label, "cash_in": side_in, "cash_out": side_out, "net": side_in - side_out})

    customers = Customer.objects.filter(accounting_entries__isnull=False).distinct().order_by("account_brand", "contact_name")

    return render(
        request,
        "crm/accounting_cash_flow_dashboard.html",
        {
            "filter_values": filter_values,
            "currency_options": ["CAD", "BDT", "USD"],
            "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
            "customers": customers,
            "supplier_choices": supplier_choices,
            "opening_cash_balance": opening_cash_balance,
            "cash_received_from_customers": cash_received_from_customers,
            "cash_paid_to_suppliers": cash_paid_to_suppliers,
            "operating_expenses_paid": operating_expenses_paid,
            "loan_payments": loan_payments,
            "tax_payments": tax_payments,
            "net_cash_flow": net_cash_flow,
            "closing_cash_balance": closing_cash_balance,
            "daily_rows": _cf_daily_rows(period_entries),
            "weekly_rows": _cf_weekly_rows(period_entries),
            "monthly_rows": _cf_monthly_rows(period_entries),
            "top_inflow_rows": _cf_group_cash(inflow_entries, _pl_customer_label),
            "top_outflow_rows": _cf_group_cash(outflow_entries, _ap_supplier_label),
            "side_rows": side_rows,
            "forecast_receivables": forecast_receivables,
            "forecast_payables": forecast_payables,
            "forecast_net": forecast_net,
            "forecast_closing_cash": forecast_closing_cash,
            "forecast_invoice_count": len(forecast_invoices),
            "forecast_payable_count": len(forecast_payable_rows),
            "low_cash_label": low_cash_label,
            "low_cash_tone": low_cash_tone,
            "entry_count": len(period_entries),
            "opening_entry_count": len(opening_entries),
        },
    )


# --------------------
# CANADA MASTER
# --------------------
@login_required
@ca_required
def accounting_ca_master(request):
    rate_row = _get_rate_row()

    if request.method == "POST" and request.POST.get("action") == "update_rate":
        new_rate = (request.POST.get("cad_to_bdt") or "").strip()
        try:
            rate_row.cad_to_bdt = Decimal(new_rate)
            rate_row.save(update_fields=["cad_to_bdt"])
            messages.success(request, "Exchange rate updated.")
        except Exception:
            messages.error(request, "Invalid exchange rate.")
        return redirect("accounting_ca_master")

    send_form = SendMoneyToBdForm()

    if request.method == "POST" and request.POST.get("action") == "send_to_bd":
        send_form = SendMoneyToBdForm(request.POST)
        if send_form.is_valid():
            d = send_form.cleaned_data["date"]
            cad_amount = send_form.cleaned_data["cad_amount"]
            sent_method = send_form.cleaned_data["sent_method"]
            note = (send_form.cleaned_data.get("note") or "").strip()

            cad_to_bdt = rate_row.cad_to_bdt or Decimal("0")
            if cad_to_bdt <= 0:
                messages.error(request, "Please set the exchange rate first.")
                return redirect("accounting_ca_master")

            bdt_amount = (cad_amount * cad_to_bdt).quantize(Decimal("0.01"))
            ref = uuid4().hex[:10]

            desc_ca = f"Send to BD | Method: {sent_method} | Ref: {ref} | {note}".strip()
            desc_bd = f"Receive from CA | Method: {sent_method} | Ref: {ref} | {note}".strip()

            with transaction.atomic():
                ca_entry = AccountingEntry.objects.create(
                    date=d,
                    side="CA",
                    direction="OUT",
                    status="PAID",
                    main_type="TRANSFER",
                    sub_type="Send to BD",
                    transfer_ref=ref,
                    currency="CAD",
                    amount_original=cad_amount,
                    rate_to_cad=Decimal("1"),
                    rate_to_bdt=cad_to_bdt,
                    description=desc_ca,
                    created_by=request.user,
                )

                bd_entry = AccountingEntry.objects.create(
                    date=d,
                    side="BD",
                    direction="IN",
                    status="PAID",
                    main_type="TRANSFER",
                    sub_type="Receive from CA",
                    transfer_ref=ref,
                    currency="BDT",
                    amount_original=bdt_amount,
                    rate_to_cad=(Decimal("1") / cad_to_bdt if cad_to_bdt else Decimal("0")),
                    rate_to_bdt=Decimal("1"),
                    description=desc_bd,
                    created_by=request.user,
                )

                _audit(ca_entry, "CREATE", request.user, after=_entry_snapshot(ca_entry), note="Transfer CA")
                _audit(bd_entry, "CREATE", request.user, after=_entry_snapshot(bd_entry), note="Transfer BD")

            messages.success(request, "Transfer saved.")
            return redirect("accounting_ca_master")

        messages.error(request, "Please fix the form errors and try again.")

    return render(request, "crm/accounting_ca_master.html", {"rate_row": rate_row, "send_form": send_form})
from decimal import Decimal
import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.db.models import Q

from .models import AccountingEntry
from .views_accounting import ca_required  # if your decorator lives elsewhere, keep your current import


@login_required
@ca_required
def accounting_ca_grid(request):
    qs = AccountingEntry.objects.filter(side="CA").order_by("-date", "-id")

    month = (request.GET.get("month") or "").strip()
    category = (request.GET.get("category") or "").strip()
    q = (request.GET.get("q") or "").strip()
    export = (request.GET.get("export") or "").strip()

    if month:
        try:
            y_str, m_str = month.split("-")
            qs = qs.filter(date__year=int(y_str), date__month=int(m_str))
        except Exception:
            pass

    if category:
        qs = qs.filter(main_type=category)

    if q:
        qs = qs.filter(
            Q(description__icontains=q)
            | Q(internal_note__icontains=q)
            | Q(transfer_ref__icontains=q)
            | Q(sub_type__icontains=q)
            | Q(main_type__icontains=q)
        )

    rows = []
    total_out = Decimal("0")
    total_in = Decimal("0")

    for e in qs:
        direction = (e.direction or "").upper().strip()

        amount_original = e.amount_original or Decimal("0")
        amount_cad = e.amount_cad or Decimal("0")

        # CA grid should show CAD.
        # If amount_cad is still 0 (like your DB shows), fall back to amount_original.
        amount = amount_cad if amount_cad != 0 else amount_original

        money_out = Decimal("0")
        money_in = Decimal("0")

        if direction == "OUT":
            money_out = amount
            total_out += amount
        elif direction == "IN":
            money_in = amount
            total_in += amount

        cat = (e.main_type or "").strip()
        sub = (e.sub_type or "").strip()
        category_text = f"{cat} / {sub}" if sub else cat

        rows.append(
            {
                "date": e.date,
                "description": e.description or "",
                "category": category_text,
                "money_out": money_out,
                "money_in": money_in,
                "currency": e.currency or "CAD",
                "side": e.side or "CA",
            }
        )

    months = AccountingEntry.objects.filter(side="CA").dates("date", "month", order="DESC")
    categories = (
        AccountingEntry.objects.filter(side="CA")
        .values_list("main_type", flat=True)
        .distinct()
        .order_by("main_type")
    )

    if export == "1":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="ca_accounting_grid.csv"'
        w = csv.writer(resp)
        w.writerow(["Date", "Description", "Category", "Money Out CAD", "Money In CAD", "Currency", "Side"])

        for r in rows:
            w.writerow(
                [
                    r["date"],
                    r["description"],
                    r["category"],
                    r["money_out"],
                    r["money_in"],
                    r["currency"],
                    r["side"],
                ]
            )

        w.writerow([])
        w.writerow(["Totals", "", "", total_out, total_in, "", ""])
        return resp

    return render(
        request,
        "crm/accounting_ca_grid.html",
        {
            "rows": rows,
            "months": months,
            "categories": categories,
            "month": month,
            "category": category,
            "q": q,
            "total_out": total_out,
            "total_in": total_in,
        },
    )
# --------------------
# BD DAILY
# --------------------
BD_MONTHLY_TARGET_BDT = Decimal("0")  # replace later if you want from DB

@login_required
@bd_required
def accounting_bd_daily(request):
    today = timezone.localdate()

    y = _parse_int(request.GET.get("year") or str(today.year)) or today.year
    m = _parse_int(request.GET.get("month") or str(today.month)) or today.month

    if request.method == "POST":
        form = BDDailyEntryForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Bangladesh daily entry saved.")
            return redirect(f"/accounting/bd-daily/?year={y}&month={m}")

        # ADD THIS LINE
        print("BD DAILY FORM ERRORS:", form.errors)

        messages.error(request, "Please fix the errors and try again.")
    else:
        form = BDDailyEntryForm(initial={"date": today}, user=request.user)

    qs = AccountingEntry.objects.filter(
        side="BD",
        currency="BDT",
        date__year=y,
        date__month=m,
    )

    total_in = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_bdt"), Decimal("0"))
    )["x"]

    total_out = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_bdt"), Decimal("0"))
    )["x"]

    net_bdt = total_in - total_out
    entries = qs.order_by("-date", "-id")[:300]
    remaining_month_bdt = BD_MONTHLY_TARGET_BDT - total_out

    return render(
        request,
        "crm/accounting_bd_daily.html",
        {
            "form": form,
            "entries": entries,
            "today": today,
            "filter_year": str(y),
            "filter_month": str(m),
            "monthly_target_bdt": BD_MONTHLY_TARGET_BDT,
            "this_month_spent_bdt": total_out,
            "remaining_month_bdt": remaining_month_bdt,
            "net_bdt": net_bdt,
        },
    )


# --------------------
# BD GRID
# --------------------
# crm/views_accounting.py

from decimal import Decimal
from django.contrib import messages
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import BDDailyEntryForm
from .models import AccountingEntry, AccountingAttachment


@login_required
@bd_required
def accounting_bd_daily(request):
    today = timezone.localdate()

    def _parse_int(x):
        try:
            return int(str(x).strip())
        except Exception:
            return None

    y = _parse_int(request.GET.get("year") or str(today.year)) or today.year
    m = _parse_int(request.GET.get("month") or str(today.month)) or today.month

    if request.method == "POST":
        form = BDDailyEntryForm(request.POST, request.FILES, user=request.user)

        if form.is_valid():
            entry = form.save()

            # Save attachments if any
            files = request.FILES.getlist("attachments")
            for f in files:
                AccountingAttachment.objects.create(
                    entry=entry,
                    file=f,
                    uploaded_by=request.user,
                    original_name=(getattr(f, "name", "") or "")[:255],
                )

            messages.success(request, "Bangladesh daily entry saved.")
            return redirect(f"/accounting/bd-daily/?year={y}&month={m}")

        # This will help you see the real reason on screen
        messages.error(request, f"Form errors: {form.errors.as_text()}")

    else:
        form = BDDailyEntryForm(initial={"date": today}, user=request.user)

    qs = AccountingEntry.objects.filter(
        side="BD",
        currency="BDT",
        date__year=y,
        date__month=m,
    )

    total_in = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_bdt"), Decimal("0"))
    )["x"]

    total_out = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_bdt"), Decimal("0"))
    )["x"]

    net_bdt = total_in - total_out

    entries = qs.order_by("-date", "-id")[:300]

    BD_MONTHLY_TARGET_BDT = Decimal("400000")
    remaining_month_bdt = BD_MONTHLY_TARGET_BDT - total_out

    return render(
        request,
        "crm/accounting_bd_daily.html",
        {
            "form": form,
            "entries": entries,
            "today": today,
            "filter_year": str(y),
            "filter_month": str(m),
            "monthly_target_bdt": BD_MONTHLY_TARGET_BDT,
            "this_month_spent_bdt": total_out,
            "remaining_month_bdt": remaining_month_bdt,
            "net_bdt": net_bdt,
        },
    )
# --------------------
# ENTRY LIST
# --------------------
@login_required
def accounting_entry_list(request):
    today = timezone.localdate()

    filter_year = (request.GET.get("year") or "").strip()
    filter_month = (request.GET.get("month") or "").strip()
    filter_side = (request.GET.get("side") or "ALL").strip()
    filter_main_type = (request.GET.get("main_type") or "ALL").strip()
    filter_q = (request.GET.get("q") or "").strip()

    qs = (
        AccountingEntry.objects.all()
        .prefetch_related("attachments")
        .select_related("production_order", "shipment")
        .order_by("-date", "-id")
    )

    if filter_year.isdigit():
        qs = qs.filter(date__year=int(filter_year))
    if filter_month.isdigit():
        qs = qs.filter(date__month=int(filter_month))
    if filter_side in ["CA", "BD"]:
        qs = qs.filter(side=filter_side)
    if filter_main_type and filter_main_type != "ALL":
        qs = qs.filter(main_type=filter_main_type)

    if filter_q:
        qs = qs.filter(
            Q(description__icontains=filter_q)
            | Q(internal_note__icontains=filter_q)
            | Q(sub_type__icontains=filter_q)
            | Q(main_type__icontains=filter_q)
            | Q(transfer_ref__icontains=filter_q)
            | Q(production_order__order_code__icontains=filter_q)
        )

    entries = list(qs[:500])

    total_ca_in = Decimal("0")
    total_ca_out = Decimal("0")
    total_bd_in = Decimal("0")
    total_bd_out = Decimal("0")
    cogs_cad = Decimal("0")

    totals_qs = qs.values(
        "side",
        "direction",
        "main_type",
        "amount_cad",
        "amount_bdt",
        "amount_original",
    )
    for row in totals_qs.iterator(chunk_size=2000):
        side = (row.get("side") or "").upper().strip()
        direction = (row.get("direction") or "").upper().strip()
        main_type = (row.get("main_type") or "").upper().strip()
        amount_original = row.get("amount_original") or Decimal("0")

        if side == "CA":
            amount = row.get("amount_cad") or Decimal("0")
            if amount == 0:
                amount = amount_original
            if direction == "IN":
                total_ca_in += amount
            elif direction == "OUT":
                total_ca_out += amount
                if main_type == "COGS":
                    cogs_cad += amount
        elif side == "BD":
            amount = row.get("amount_bdt") or Decimal("0")
            if amount == 0:
                amount = amount_original
            if direction == "IN":
                total_bd_in += amount
            elif direction == "OUT":
                total_bd_out += amount

    total_ca_net_cad = total_ca_in - total_ca_out
    total_bd_net_bdt = total_bd_in - total_bd_out
    net_cad = total_ca_net_cad
    net_bdt = total_bd_net_bdt
    revenue_cad = total_ca_in
    gross_profit_cad = revenue_cad - cogs_cad
    gross_margin_pct = (gross_profit_cad / revenue_cad * Decimal("100")) if revenue_cad else Decimal("0")
    total_income_cad = revenue_cad
    total_expense_cad = total_ca_out

    return render(
        request,
        "crm/accounting_list.html",
        {
            "entries": entries,
            "filter_year": filter_year,
            "filter_month": filter_month,
            "filter_side": filter_side,
            "filter_main_type": filter_main_type,
            "filter_q": filter_q,
            "net_cad": net_cad,
            "net_bdt": net_bdt,
            "total_ca_net_cad": total_ca_net_cad,
            "total_bd_net_bdt": total_bd_net_bdt,
            "total_income_cad": total_income_cad,
            "total_expense_cad": total_expense_cad,
            "revenue_cad": revenue_cad,
            "cogs_cad": cogs_cad,
            "gross_profit_cad": gross_profit_cad,
            "gross_margin_pct": gross_margin_pct,
            "warnings": [],
        },
    )


# --------------------
# EXPORTS
# --------------------
@login_required
def accounting_list_export_csv(request):
    qs = _entries_queryset_from_request(request)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="accounting_entries.csv"'
    w = csv.writer(resp)
    w.writerow(_export_headers())
    for e in qs:
        w.writerow(_entry_export_row(e))
    return resp


@login_required
def accounting_list_export_xlsx(request):
    qs = _entries_queryset_from_request(request)
    return _write_xlsx(qs, "accounting_entries.xlsx")


# --------------------
# MONTH CLOSE AND OPEN
# --------------------
@login_required
def accounting_close_month(request):
    if AccountingMonthLock is None:
        return HttpResponse("Month lock model not found.")

    year = _parse_int(request.POST.get("year") or request.GET.get("year"))
    month = _parse_int(request.POST.get("month") or request.GET.get("month"))
    side = (request.POST.get("side") or request.GET.get("side") or "CA").strip().upper()

    if not year or not month:
        messages.error(request, "Year and month required.")
        return redirect("accounting_entry_list")

    with transaction.atomic():
        obj, _ = AccountingMonthLock.objects.select_for_update().get_or_create(
            side=side, year=year, month=month
        )
        obj.is_closed = True
        obj.closed_at = timezone.now()
        obj.closed_by = request.user
        obj.save()

    messages.success(request, f"Month closed: {side} {year}-{month:02d}.")
    return redirect("accounting_entry_list")


@login_required
def accounting_open_month(request):
    if AccountingMonthLock is None:
        return HttpResponse("Month lock model not found.")

    year = _parse_int(request.POST.get("year") or request.GET.get("year"))
    month = _parse_int(request.POST.get("month") or request.GET.get("month"))
    side = (request.POST.get("side") or request.GET.get("side") or "CA").strip().upper()

    if not year or not month:
        messages.error(request, "Year and month required.")
        return redirect("accounting_entry_list")

    with transaction.atomic():
        obj, _ = AccountingMonthLock.objects.select_for_update().get_or_create(
            side=side, year=year, month=month
        )
        obj.is_closed = False
        obj.save()

    messages.success(request, f"Month opened: {side} {year}-{month:02d}.")
    return redirect("accounting_entry_list")


# --------------------
# FILES AND AUDIT PAGES
# --------------------
@login_required
def accounting_files(request):
    qs = AccountingAttachment.objects.select_related("entry", "uploaded_by").order_by("-uploaded_at", "-id")
    side = (request.GET.get("side") or "").strip().upper()
    if side in ["CA", "BD"]:
        qs = qs.filter(entry__side=side)

    entry_id = _parse_int(request.GET.get("entry_id"))
    if entry_id:
        qs = qs.filter(entry_id=entry_id)

    return render(request, "crm/accounting_files.html", {"files": qs})


@login_required
def accounting_audit_trail(request):
    qs = AccountingEntryAudit.objects.select_related("entry", "changed_by").order_by("-changed_at", "-id")

    action = (request.GET.get("action") or "").strip()
    if action:
        qs = qs.filter(action=action)

    entry_id = _parse_int(request.GET.get("entry_id"))
    if entry_id:
        qs = qs.filter(entry_id=entry_id)

    return render(request, "crm/accounting_audit_trail.html", {"audits": qs})


# --------------------
# DOCS
# --------------------
@login_required
def accounting_doc_upload(request):
    if request.method == "POST":
        form = AccountingDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.uploaded_by = request.user
            obj.save()
            messages.success(request, "File uploaded.")
            return redirect("accounting_doc_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = AccountingDocumentForm()

    return render(request, "crm/accounting_doc_upload.html", {"form": form})


@login_required
def accounting_doc_list(request):
    q = (request.GET.get("q") or "").strip()
    side = (request.GET.get("side") or "").strip().upper()

    qs = AccountingDocument.objects.all()

    if side in ["CA", "BD"]:
        qs = qs.filter(side=side)

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(vendor__icontains=q)
            | Q(note__icontains=q)
        )

    qs = qs.select_related("uploaded_by", "linked_entry")[:500]

    return render(request, "crm/accounting_doc_list.html", {"rows": qs, "q": q, "side": side})


@login_required
def accounting_entry_attach(request, pk):
    entry = get_object_or_404(AccountingEntry, pk=pk)

    if request.method == "POST":
        form = AccountingEntryAttachForm(request.POST, request.FILES)
        if form.is_valid():
            files = form.cleaned_data["files"]
            note = (form.cleaned_data.get("note") or "").strip()

            for f in files:
                AccountingAttachment.objects.create(
                    entry=entry,
                    file=f,
                    original_name=(getattr(f, "name", "") or "File")[:255],
                    uploaded_by=request.user,
                    note=note,
                )

            messages.success(request, "Files uploaded.")
            return redirect("accounting_entry_list")

        messages.error(request, "Please fix the errors below.")
    else:
        form = AccountingEntryAttachForm()

    return render(request, "crm/accounting_entry_attach.html", {"entry": entry, "form": form})

@login_required
@bd_required
def accounting_bd_dashboard(request):
    year_raw = (request.GET.get("year") or "").strip()
    month_raw = (request.GET.get("month") or "").strip()

    qs = AccountingEntry.objects.filter(side="BD")

    if year_raw.isdigit():
        qs = qs.filter(date__year=int(year_raw))

    if month_raw.isdigit():
        m = int(month_raw)
        if 1 <= m <= 12:
            qs = qs.filter(date__month=m)

    total_in_bdt = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_original"), Decimal("0"))
    )["x"]

    total_out_bdt = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_original"), Decimal("0"))
    )["x"]

    net_bdt = total_in_bdt - total_out_bdt

    return render(
        request,
        "crm/accounting_bd_dashboard.html",
        {
            "filter_year": year_raw,
            "filter_month": month_raw,
            "entries": qs.order_by("-date", "-id")[:200],
            "total_in_bdt": total_in_bdt,
            "total_out_bdt": total_out_bdt,
            "net_bdt": net_bdt,
        },
    )

from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.utils import timezone

from .decorators import bd_required
from .models import AccountingEntry


@login_required
@bd_required
def accounting_bd_grid(request):
    today = timezone.localdate()

    year_raw = (request.GET.get("year") or str(today.year)).strip()
    month_raw = (request.GET.get("month") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = (
        AccountingEntry.objects.filter(side="BD")
        .order_by("-date", "-id")
    )

    if year_raw.isdigit():
        qs = qs.filter(date__year=int(year_raw))

    month = ""
    if month_raw.isdigit():
        m = int(month_raw)
        if 1 <= m <= 12:
            month = str(m)
            qs = qs.filter(date__month=m)

    if q:
        qs = qs.filter(
            Q(description__icontains=q)
            | Q(sub_type__icontains=q)
            | Q(main_type__icontains=q)
            | Q(transfer_ref__icontains=q)
        )

    total_in_bdt = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_original"), Decimal("0"))
    )["x"]

    total_out_bdt = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_original"), Decimal("0"))
    )["x"]

    net_bdt = total_in_bdt - total_out_bdt

    return render(
        request,
        "crm/accounting_bd_grid.html",
        {
            "entries": list(qs[:500]),
            "filter_year": year_raw,
            "filter_month": month,
            "q": q,
            "total_in_bdt": total_in_bdt,
            "total_out_bdt": total_out_bdt,
            "net_bdt": net_bdt,
            "monthly_target_bdt": Decimal("0"),
        },
    )


@login_required
@bd_required
def accounting_bd_dashboard(request):
    year_raw = (request.GET.get("year") or "").strip()
    month_raw = (request.GET.get("month") or "").strip()

    qs = AccountingEntry.objects.filter(side="BD")

    if year_raw.isdigit():
        qs = qs.filter(date__year=int(year_raw))

    if month_raw.isdigit():
        m = int(month_raw)
        if 1 <= m <= 12:
            qs = qs.filter(date__month=m)

    total_in_bdt = qs.filter(direction="IN").aggregate(
        x=Coalesce(Sum("amount_original"), Decimal("0"))
    )["x"]

    total_out_bdt = qs.filter(direction="OUT").aggregate(
        x=Coalesce(Sum("amount_original"), Decimal("0"))
    )["x"]

    net_bdt = total_in_bdt - total_out_bdt

    return render(
        request,
        "crm/accounting_bd_dashboard.html",
        {
            "filter_year": year_raw,
            "filter_month": month_raw,
            "entries": qs.order_by("-date", "-id")[:200],
            "total_in_bdt": total_in_bdt,
            "total_out_bdt": total_out_bdt,
            "net_bdt": net_bdt,
        },
    )


from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from .models import AccountingEntry, ProductionOrder


def _parse_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


SWING_SUB_TYPE = "Swing"


def _entry_amount_cad(row, cad_to_bdt):
    amt_cad = row.get("amount_cad") or Decimal("0")
    if amt_cad != 0:
        return amt_cad
    currency = (row.get("currency") or "").upper().strip()
    amt_orig = row.get("amount_original") or Decimal("0")
    if currency == "CAD":
        return amt_orig
    if currency == "BDT" and cad_to_bdt:
        return (amt_orig / cad_to_bdt).quantize(Decimal("0.01"))
    return Decimal("0")


def _entry_amount_bdt(row, cad_to_bdt):
    amt_bdt = row.get("amount_bdt") or Decimal("0")
    if amt_bdt != 0:
        return amt_bdt
    currency = (row.get("currency") or "").upper().strip()
    amt_orig = row.get("amount_original") or Decimal("0")
    if currency == "BDT":
        return amt_orig
    if currency == "CAD" and cad_to_bdt:
        return (amt_orig * cad_to_bdt).quantize(Decimal("0.01"))
    return Decimal("0")


def production_profit_rows(year=None, month=None):
    base_entries = AccountingEntry.objects.filter(production_order_id__isnull=False)
    if year:
        base_entries = base_entries.filter(date__year=year)
    if month:
        base_entries = base_entries.filter(date__month=month)

    order_ids = set(
        base_entries.values_list("production_order_id", flat=True).distinct()
    )

    po_qs = ProductionOrder.objects.all()
    if year:
        po_qs = po_qs.filter(created_at__year=year)
    if month:
        po_qs = po_qs.filter(created_at__month=month)
    po_qs = po_qs.filter(
        Q(actual_total_cost_bdt__gt=0)
        | Q(production_total_cost_bdt__gt=0)
        | Q(production_sewing_cost_bdt__gt=0)
    )
    order_ids.update(po_qs.values_list("id", flat=True))

    order_ids = sorted({oid for oid in order_ids if oid})
    if not order_ids:
        return []

    rate_row = _get_rate_row()
    cad_to_bdt = rate_row.cad_to_bdt or Decimal("0")

    stats = {}
    for oid in order_ids:
        stats[oid] = {
            "revenue_cad": Decimal("0"),
            "swing_cad": Decimal("0"),
            "swing_bdt": Decimal("0"),
            "cost_cad_from_entries": Decimal("0"),
        }

    entries_qs = (
        base_entries.filter(production_order_id__in=order_ids)
        .values(
            "production_order_id",
            "side",
            "direction",
            "main_type",
            "sub_type",
            "currency",
            "amount_original",
            "amount_cad",
            "amount_bdt",
        )
    )

    for row in entries_qs.iterator(chunk_size=2000):
        oid = row.get("production_order_id")
        if not oid or oid not in stats:
            continue
        side = (row.get("side") or "").upper().strip()
        direction = (row.get("direction") or "").upper().strip()
        main_type = (row.get("main_type") or "").upper().strip()
        sub_type = (row.get("sub_type") or "").strip()

        if side == "CA" and direction == "IN":
            amount_cad = _entry_amount_cad(row, cad_to_bdt)
            stats[oid]["revenue_cad"] += amount_cad
            if sub_type.lower() == SWING_SUB_TYPE.lower():
                stats[oid]["swing_cad"] += amount_cad

        if side == "BD" and direction == "OUT":
            amount_cad = _entry_amount_cad(row, cad_to_bdt)
            amount_bdt = _entry_amount_bdt(row, cad_to_bdt)
            if main_type in ["COGS", "EXPENSE"]:
                stats[oid]["cost_cad_from_entries"] += amount_cad
            if sub_type.lower() == SWING_SUB_TYPE.lower():
                stats[oid]["swing_bdt"] += amount_bdt

    orders_map = ProductionOrder.objects.in_bulk(order_ids)
    order_type_labels = dict(getattr(ProductionOrder, "ORDER_TYPE_CHOICES", []))

    rows = []
    for oid in order_ids:
        po = orders_map.get(oid)

        order_code = str(oid)
        product_type = ""
        pcs = 0
        order_type = ""
        order_type_label = ""
        bd_sewing_bdt = Decimal("0")
        bd_total_cost_bdt = Decimal("0")

        if po:
            order_code = po.order_code or str(po.id)
            product_type = (po.style_name or po.title or "").strip()
            pcs = getattr(po, "qty_total", 0) or 0
            order_type = (po.order_type or "").strip()
            order_type_label = order_type_labels.get(order_type, "")
            bd_sewing_bdt = po.production_sewing_cost_bdt or Decimal("0")
            bd_total_cost_bdt = (
                po.actual_total_cost_bdt
                or po.production_total_cost_bdt
                or Decimal("0")
            )

        revenue = stats[oid]["revenue_cad"]
        swing_cad = stats[oid]["swing_cad"]
        swing_bdt = stats[oid]["swing_bdt"]

        if bd_total_cost_bdt and cad_to_bdt and cad_to_bdt > 0:
            cost_cad = (bd_total_cost_bdt / cad_to_bdt).quantize(Decimal("0.01"))
        else:
            cost_cad = stats[oid]["cost_cad_from_entries"]

        profit = revenue - cost_cad
        margin = (profit / revenue * Decimal("100")) if revenue else Decimal("0")

        rows.append(
            {
                "production_order_id": oid,
                "order_code": order_code,
                "product_type": product_type,
                "pcs": pcs,
                "order_type": order_type,
                "order_type_label": order_type_label,
                "revenue_cad": revenue,
                "swing_cad": swing_cad,
                "swing_bdt": swing_bdt,
                "bd_sewing_bdt": bd_sewing_bdt,
                "bd_total_cost_bdt": bd_total_cost_bdt,
                "cost_cad": cost_cad,
                "profit_cad": profit,
                "margin_pct": margin,
            }
        )

    rows.sort(key=lambda r: r.get("profit_cad", Decimal("0")), reverse=True)
    return rows


@login_required
def production_profit_report(request):
    today = timezone.localdate()
    y = _parse_int(request.GET.get("year") or str(today.year)) or today.year
    m = _parse_int(request.GET.get("month") or str(today.month)) or today.month

    rows = production_profit_rows(year=y, month=m)

    return render(
        request,
        "crm/production_profit_report.html",
        {
            "rows": rows,
            "filter_year": str(y),
            "filter_month": str(m),
            "SWING_SUB_TYPE": SWING_SUB_TYPE,
        },
    )

from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import AccountingEntry


AI_RULES = [
    ("COGS", "Fabric", ["fabric", "knit", "dye", "yarn"]),
    ("COGS", "Trims", ["label", "tag", "button", "zip", "thread"]),
    ("COGS", "Printing", ["print", "screen", "dtg", "heat"]),
    ("COGS", "Embroidery", ["embroidery", "embro", "dst"]),
    ("EXPENSE", "Rent", ["rent"]),
    ("EXPENSE", "Utilities", ["electric", "utility", "water", "gas", "internet"]),
    ("EXPENSE", "Transport", ["transport", "fuel", "truck", "courier"]),
    ("EXPENSE", "Repair", ["repair", "maintenance", "service"]),
    ("EXPENSE", "Food", ["tea", "snack", "food"]),
    ("TRANSFER", "Send to BD", ["send to bd", "transfer"]),
    ("TRANSFER", "Receive from CA", ["receive from ca"]),
]


def _parse_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def ai_suggest_main_sub(description: str):
    text = (description or "").lower().strip()
    if not text:
        return None

    best = None
    best_hits = 0

    for main_type, sub_type, keys in AI_RULES:
        hits = 0
        for k in keys:
            if k in text:
                hits += 1

        if hits > best_hits:
            best_hits = hits
            best = {"main_type": main_type, "sub_type": sub_type, "hits": hits}

    if not best or best_hits == 0:
        return None

    if best_hits >= 3:
        conf = 90
    elif best_hits == 2:
        conf = 75
    else:
        conf = 60

    best["confidence"] = conf
    return best


@login_required
def accounting_ai_audit(request):
    today = timezone.localdate()

    y = _parse_int(request.GET.get("year") or str(today.year)) or today.year
    m = _parse_int(request.GET.get("month") or "") or None
    side = (request.GET.get("side") or "ALL").strip()

    qs = AccountingEntry.objects.all().order_by("-date", "-id")
    qs = qs.filter(date__year=y)

    if m:
        qs = qs.filter(date__month=m)

    if side in ["CA", "BD"]:
        qs = qs.filter(side=side)

    qs = qs[:600]

    issues = []
    by_type = defaultdict(list)

    for e in qs:
        if not e.date:
            issues.append({"code": "missing_date", "title": "Missing date", "entry": e})
            by_type["missing_date"].append(e)

        if not (e.amount_original and e.amount_original > 0):
            issues.append({"code": "bad_amount", "title": "Amount missing", "entry": e})
            by_type["bad_amount"].append(e)

        sug = ai_suggest_main_sub(e.description or "")
        if sug and ((e.main_type or "") != sug["main_type"]):
            issues.append(
                {"code": "ai_suggestion", "title": "Possible better category", "entry": e, "sug": sug}
            )
            by_type["ai_suggestion"].append(e)

    top_types = []
    for code, rows in by_type.items():
        top_types.append({"code": code, "title": code, "count": len(rows)})
    top_types.sort(key=lambda x: x["count"], reverse=True)

    return render(
        request,
        "crm/accounting_ai_audit.html",
        {
            "filter_year": str(y),
            "filter_month": str(m or ""),
            "filter_side": side,
            "issues": issues[:200],
            "top_types": top_types,
            "total_issues": len(issues),
            "total_entries_checked": len(qs),
        },
    )


@login_required
def accounting_ai_suggest(request):
    desc = (request.GET.get("description") or "").strip()
    suggestion = ai_suggest_main_sub(desc)

    if not suggestion:
        return JsonResponse({"main_type": "OTHER", "sub_type": "", "confidence": 0})

    return JsonResponse(
        {
            "main_type": suggestion["main_type"],
            "sub_type": suggestion["sub_type"],
            "confidence": suggestion["confidence"],
        }
    )

import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from .models import AccountingEntry


@login_required
def accounting_bd_grid_export_csv(request):
    qs = AccountingEntry.objects.filter(side="BD").order_by("-date", "-id")

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="bd_accounting_grid.csv"'
    w = csv.writer(resp)

    w.writerow(["Date", "Description", "Main Type", "Sub Type", "Direction", "Amount Original", "Currency"])

    for e in qs:
        w.writerow([
            e.date,
            (e.description or "").strip(),
            (e.main_type or "").strip(),
            (e.sub_type or "").strip(),
            (e.direction or "").strip(),
            e.amount_original or Decimal("0"),
            (e.currency or "").strip(),
        ])

    return resp


@login_required
def accounting_bd_grid_export_xlsx(request):
    try:
        from openpyxl import Workbook
    except Exception:
        return HttpResponse("openpyxl is not installed", status=500)

    qs = AccountingEntry.objects.filter(side="BD").order_by("-date", "-id")

    wb = Workbook()
    ws = wb.active
    ws.title = "BD Grid"

    ws.append(["Date", "Description", "Main Type", "Sub Type", "Direction", "Amount Original", "Currency"])

    for e in qs:
        ws.append([
            e.date,
            (e.description or "").strip(),
            (e.main_type or "").strip(),
            (e.sub_type or "").strip(),
            (e.direction or "").strip(),
            float(e.amount_original or Decimal("0")),
            (e.currency or "").strip(),
        ])

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = 'attachment; filename="bd_accounting_grid.xlsx"'
    wb.save(resp)
    return resp

@login_required
def bd_staff_list(request):
    qs = BDStaff.objects.all().order_by("name")
    return render(request, "crm/bd_staff_list.html", {"rows": qs})


@login_required
def bd_staff_add(request):
    if request.method == "POST":
        form = BDStaffForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Staff added.")
            return redirect("bd_staff_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = BDStaffForm()

    return render(request, "crm/bd_staff_form.html", {"form": form, "mode": "add"})


@login_required
def bd_staff_edit(request, pk):
    obj = get_object_or_404(BDStaff, pk=pk)

    if request.method == "POST":
        form = BDStaffForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Staff updated.")
            return redirect("bd_staff_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = BDStaffForm(instance=obj)

    return render(request, "crm/bd_staff_form.html", {"form": form, "mode": "edit", "obj": obj})


@login_required
def bd_staff_month_list(request):
    qs = BDStaffMonth.objects.select_related("staff").order_by("-year", "-month", "staff__name")
    return render(request, "crm/bd_staff_month_list.html", {"rows": qs})


@login_required
def bd_staff_month_generate(request):
    if request.method == "POST":
        year = int(request.POST.get("year") or 0)
        month = int(request.POST.get("month") or 0)

        if year < 2000 or month < 1 or month > 12:
            messages.error(request, "Invalid year or month.")
            return redirect("bd_staff_month_list")

        staff_qs = BDStaff.objects.filter(is_active=True)

        created_count = 0
        for s in staff_qs:
            obj, created = BDStaffMonth.objects.get_or_create(
                staff=s,
                year=year,
                month=month,
                defaults={"base_salary_bdt": s.base_salary_bdt},
            )
            if created:
                created_count += 1

        messages.success(request, f"Generated {created_count} rows.")
        return redirect("bd_staff_month_list")

    return render(request, "crm/bd_staff_month_generate.html")


@login_required
def bd_staff_month_edit(request, pk):
    obj = get_object_or_404(BDStaffMonth, pk=pk)

    if request.method == "POST":
        form = BDStaffMonthForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Month updated.")
            return redirect("bd_staff_month_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = BDStaffMonthForm(instance=obj)

    return render(request, "crm/bd_staff_month_form.html", {"form": form, "obj": obj})
