# crm/views_accounting.py (or wherever your accounting views live)

import csv
import io
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

from .models import (
    AccountingEntry,
    AccountingAttachment,
    AccountingEntryAudit,
    AccountingDocument,
    ExchangeRate,
    BDStaff,
    BDStaffMonth,
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
