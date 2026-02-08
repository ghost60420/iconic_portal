from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import AccountingDocsUploadForm
from .models import AccountingEntry, AccountingAttachment, ExchangeRate


def _latest_cad_to_bdt():
    row = ExchangeRate.objects.order_by("-updated_at").first()
    if row and row.cad_to_bdt and row.cad_to_bdt > 0:
        return Decimal(str(row.cad_to_bdt))
    return Decimal("0")


def is_ca_user(user) -> bool:
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name__in=["CA", "Canada"]).exists()
    )


def is_bd_user(user) -> bool:
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name__in=["BD", "Bangladesh"]).exists()
    )


def _save_files(entry, files, uploaded_by=None):
    for f in files:
        AccountingAttachment.objects.create(
            entry=entry,
            file=f,
            original_name=getattr(f, "name", "File"),
            uploaded_by=uploaded_by,
        )


@login_required
def accounting_docs_upload_ca(request):
    if not is_ca_user(request.user):
        return redirect("accounting_home")

    if request.method == "POST":
        form = AccountingDocsUploadForm(request.POST, request.FILES)
        if form.is_valid():
            date_val = form.cleaned_data.get("date") or timezone.localdate()
            cad_to_bdt = _latest_cad_to_bdt()

            amount = form.cleaned_data.get("amount") or 0
            invoice_number = (form.cleaned_data.get("invoice_number") or "").strip()
            note = (form.cleaned_data.get("note") or "").strip()
            sub_type = (form.cleaned_data.get("sub_type") or "").strip()

            desc_parts = []
            if invoice_number:
                desc_parts.append(f"Invoice {invoice_number}")
            if note:
                desc_parts.append(note)
            description = " | ".join(desc_parts)

            with transaction.atomic():
                entry = AccountingEntry.objects.create(
                    date=date_val,
                    side="CA",
                    currency="CAD",
                    direction=form.cleaned_data["direction"],
                    main_type=form.cleaned_data["main_type"],
                    sub_type=sub_type,
                    description=description,
                    amount_original=amount,
                    rate_to_cad=Decimal("1"),
                    rate_to_bdt=cad_to_bdt if cad_to_bdt > 0 else Decimal("0"),
                    created_by=request.user,
                )

                _save_files(entry, request.FILES.getlist("files"), uploaded_by=request.user)

            messages.success(request, "Uploaded. Saved to the accounting grid.")
            return redirect("accounting_entry_list")
    else:
        form = AccountingDocsUploadForm(
            initial={
                "date": timezone.localdate(),
                "direction": "OUT",
                "main_type": "EXPENSE",
            }
        )

    return render(request, "crm/accounting_docs_upload.html", {"form": form, "side": "CA"})


@login_required
def accounting_docs_upload_bd(request):
    if not is_bd_user(request.user):
        return redirect("accounting_home")

    if request.method == "POST":
        form = AccountingDocsUploadForm(request.POST, request.FILES)
        if form.is_valid():
            date_val = form.cleaned_data.get("date") or timezone.localdate()
            cad_to_bdt = _latest_cad_to_bdt()

            amount = form.cleaned_data.get("amount") or 0
            invoice_number = (form.cleaned_data.get("invoice_number") or "").strip()
            note = (form.cleaned_data.get("note") or "").strip()
            sub_type = (form.cleaned_data.get("sub_type") or "").strip()

            desc_parts = []
            if invoice_number:
                desc_parts.append(f"Invoice {invoice_number}")
            if note:
                desc_parts.append(note)
            description = " | ".join(desc_parts)

            with transaction.atomic():
                entry = AccountingEntry.objects.create(
                    date=date_val,
                    side="BD",
                    currency="BDT",
                    direction=form.cleaned_data["direction"],
                    main_type=form.cleaned_data["main_type"],
                    sub_type=sub_type,
                    description=description,
                    amount_original=amount,
                    rate_to_bdt=Decimal("1"),
                    rate_to_cad=(Decimal("1") / cad_to_bdt).quantize(Decimal("0.000001"))
                    if cad_to_bdt > 0
                    else Decimal("0"),
                    created_by=request.user,
                )

                _save_files(entry, request.FILES.getlist("files"), uploaded_by=request.user)

            messages.success(request, "Uploaded. Saved to the accounting grid.")
            return redirect("accounting_entry_list")
    else:
        form = AccountingDocsUploadForm(
            initial={
                "date": timezone.localdate(),
                "direction": "OUT",
                "main_type": "EXPENSE",
            }
        )

    return render(request, "crm/accounting_docs_upload.html", {"form": form, "side": "BD"})
