from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import AccountingDocsUploadForm
from .models import AccountingEntry, AccountingAttachment


def is_ca_user(user) -> bool:
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name__in=["CA", "Canada"]).exists()
    )


def is_bd_user(user) -> bool:
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name__in=["BD", "Bangladesh"]).exists()
    )


def _save_files(entry, files):
    for f in files:
        AccountingAttachment.objects.create(
            entry=entry,
            file=f,
            original_name=getattr(f, "name", "File"),
        )


@login_required
def accounting_docs_upload_ca(request):
    if not is_ca_user(request.user):
        return redirect("accounting_home")

    if request.method == "POST":
        form = AccountingDocsUploadForm(request.POST, request.FILES)
        if form.is_valid():
            date_val = form.cleaned_data.get("date") or timezone.localdate()

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
                    amount_cad=amount,
                    amount_bdt=0,
                )

                _save_files(entry, request.FILES.getlist("files"))

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
                    amount_cad=0,
                    amount_bdt=amount,
                )

                _save_files(entry, request.FILES.getlist("files"))

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