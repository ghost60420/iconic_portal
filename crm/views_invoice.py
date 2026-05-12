# crm/views_invoice.py

from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    AccountingEntry,
    AccountingEntryAudit,
    AccountingMonthClose,
    AccountingMonthLock,
    ExchangeRate,
    Invoice,
    InvoicePayment,
    ProductionOrder,
)
from .forms import InvoiceForm, InvoicePaymentForm


def superuser_only(user):
    return bool(user and user.is_superuser)


def _d(v):
    try:
        return Decimal(str(v)) if v is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def _calc_totals(inv: Invoice) -> None:
    subtotal = _d(inv.subtotal)
    shipping = _d(inv.shipping_amount)
    discount = _d(inv.discount_amount)
    tax = _d(inv.tax_amount)

    total = subtotal + shipping + tax - discount
    if total < 0:
        total = Decimal("0")
    inv.total_amount = total

    paid = _d(inv.paid_amount)
    if paid <= 0:
        if inv.status not in ("draft", "sent", "cancelled"):
            inv.status = "sent"
    else:
        if paid >= total and total > 0:
            inv.status = "paid"
        elif total > 0:
            inv.status = "partial"


def _invoice_payment_side(inv: Invoice) -> str:
    region = (getattr(inv, "invoice_region", "") or "").upper().strip()
    if region in {"CA", "BD"}:
        return region
    currency = (getattr(inv, "currency", "") or "").upper().strip()
    return "BD" if currency == "BDT" else "CA"


def _latest_cad_to_bdt() -> Decimal:
    row = ExchangeRate.objects.order_by("-updated_at").first()
    return row.cad_to_bdt if row and row.cad_to_bdt else Decimal("0")


def _payment_rate_initial(currency: str) -> dict:
    currency = (currency or "").upper().strip()
    cad_to_bdt = _latest_cad_to_bdt()
    data = {"rate_to_cad": Decimal("0"), "rate_to_bdt": Decimal("0")}

    if currency == "CAD":
        data["rate_to_cad"] = Decimal("1")
        if cad_to_bdt > 0:
            data["rate_to_bdt"] = cad_to_bdt
    elif currency == "BDT":
        data["rate_to_bdt"] = Decimal("1")
        if cad_to_bdt > 0:
            data["rate_to_cad"] = (Decimal("1") / cad_to_bdt).quantize(Decimal("0.000001"))

    return data


def _is_accounting_month_closed(payment_date, side: str) -> bool:
    if not payment_date:
        return False

    year = payment_date.year
    month = payment_date.month
    side = (side or "").upper().strip()

    if AccountingMonthClose.objects.filter(
        year=year,
        month=month,
        is_closed=True,
        side__in=[side, "ALL"],
    ).exists():
        return True

    lock_fields = {field.name for field in AccountingMonthLock._meta.fields}
    lock_filter = {"year": year, "month": month, "is_closed": True}
    if "side" in lock_fields and side:
        lock_filter["side"] = side
    return AccountingMonthLock.objects.filter(**lock_filter).exists()


def _entry_snapshot(entry: AccountingEntry) -> dict:
    return {
        "id": entry.id,
        "date": str(entry.date) if entry.date else "",
        "side": entry.side,
        "direction": entry.direction,
        "status": entry.status,
        "main_type": entry.main_type,
        "sub_type": entry.sub_type,
        "currency": entry.currency,
        "amount_original": str(entry.amount_original or ""),
        "amount_cad": str(entry.amount_cad or ""),
        "amount_bdt": str(entry.amount_bdt or ""),
        "description": entry.description or "",
        "internal_note": entry.internal_note or "",
        "customer_id": entry.customer_id or "",
        "production_order_id": entry.production_order_id or "",
    }


def _audit_accounting_entry(entry: AccountingEntry, user, note: str = "") -> None:
    try:
        AccountingEntryAudit.objects.create(
            entry=entry,
            action="CREATE",
            changed_by=user if user and user.is_authenticated else None,
            after_data=_entry_snapshot(entry),
            note=note or "",
        )
    except Exception:
        pass


def _sync_invoice_payment_status(inv: Invoice) -> None:
    total = _d(inv.total_amount)
    paid = _d(inv.paid_amount)
    if paid <= 0:
        inv.status = "sent" if inv.status != "draft" else inv.status
    elif total > 0 and paid >= total:
        inv.status = "paid"
    elif total > 0:
        inv.status = "partial"


def _next_invoice_number() -> str:
    prefix = "INV"
    latest = Invoice.objects.filter(invoice_number__startswith=prefix).order_by("-invoice_number").first()

    last_num = 0
    if latest and latest.invoice_number:
        raw = latest.invoice_number.replace(prefix, "").strip()
        try:
            last_num = int(raw)
        except Exception:
            last_num = 0

    n = last_num + 1
    cand = f"{prefix}{n:05d}"

    # avoid duplicates
    while Invoice.objects.filter(invoice_number=cand).exists():
        n += 1
        cand = f"{prefix}{n:05d}"

    return cand


@login_required
@user_passes_test(superuser_only)
def invoice_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    currency = (request.GET.get("currency") or "").strip()

    invoices = Invoice.objects.select_related("order", "customer")

    if q:
        invoices = invoices.filter(
            Q(invoice_number__icontains=q)
            | Q(order__order_code__icontains=q)
            | Q(order__title__icontains=q)
            | Q(customer__account_brand__icontains=q)
            | Q(customer__contact_name__icontains=q)
            | Q(customer__email__icontains=q)
        )

    if status:
        invoices = invoices.filter(status=status)

    if currency:
        invoices = invoices.filter(currency=currency)

    invoices = invoices.order_by("-issue_date", "-created_at")
    invoice_rows = list(invoices)
    total_amount = sum((_d(inv.total_amount) for inv in invoice_rows), Decimal("0"))
    received_amount = sum((_d(inv.paid_amount) for inv in invoice_rows), Decimal("0"))
    unpaid_balance = sum((_d(inv.balance) for inv in invoice_rows), Decimal("0"))
    open_count = sum(1 for inv in invoice_rows if inv.payment_status_key in {"unpaid", "partial", "overpaid"})

    today = timezone.localdate()
    monthly_received = (
        InvoicePayment.objects.filter(payment_date__year=today.year, payment_date__month=today.month)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    bd_received_total = (
        Invoice.objects.filter(Q(invoice_region="BD") | Q(currency="BDT")).aggregate(total=Sum("paid_amount"))["total"]
        or Decimal("0")
    )
    bd_unpaid_balance = sum(
        (_d(inv.balance) for inv in Invoice.objects.filter(Q(invoice_region="BD") | Q(currency="BDT"))),
        Decimal("0"),
    )
    production_received_rows = list(
        InvoicePayment.objects.filter(production_order__isnull=False)
        .values("production_order__order_code", "production_order__title")
        .annotate(received_bdt=Sum("amount_bdt"), received_original=Sum("amount"))
        .order_by("-received_bdt")[:8]
    )

    return render(
        request,
        "crm/invoice/invoice_list.html",
        {
            "invoices": invoice_rows,
            "q": q,
            "status": status,
            "currency": currency,
            "total_amount": total_amount,
            "received_amount": received_amount,
            "unpaid_balance": unpaid_balance,
            "open_count": open_count,
            "monthly_received": monthly_received,
            "bd_received_total": bd_received_total,
            "bd_unpaid_balance": bd_unpaid_balance,
            "production_received_rows": production_received_rows,
        },
    )


def _invoice_list_by_currency(request, currency_code: str):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    invoices = Invoice.objects.select_related("order", "customer").filter(currency=currency_code)

    if q:
        invoices = invoices.filter(
            Q(invoice_number__icontains=q)
            | Q(order__order_code__icontains=q)
            | Q(order__title__icontains=q)
            | Q(customer__account_brand__icontains=q)
            | Q(customer__contact_name__icontains=q)
            | Q(customer__email__icontains=q)
        )

    if status:
        invoices = invoices.filter(status=status)

    invoices = invoices.order_by("-issue_date", "-created_at")
    invoice_rows = list(invoices)
    total_amount = sum((_d(inv.total_amount) for inv in invoice_rows), Decimal("0"))
    received_amount = sum((_d(inv.paid_amount) for inv in invoice_rows), Decimal("0"))
    unpaid_balance = sum((_d(inv.balance) for inv in invoice_rows), Decimal("0"))
    open_count = sum(1 for inv in invoice_rows if inv.payment_status_key in {"unpaid", "partial", "overpaid"})
    today = timezone.localdate()
    monthly_received = (
        InvoicePayment.objects.filter(
            currency=currency_code,
            payment_date__year=today.year,
            payment_date__month=today.month,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    return render(
        request,
        "crm/invoice/invoice_list.html",
        {
            "invoices": invoice_rows,
            "q": q,
            "status": status,
            "currency": currency_code,
            "total_amount": total_amount,
            "received_amount": received_amount,
            "unpaid_balance": unpaid_balance,
            "open_count": open_count,
            "monthly_received": monthly_received,
            "bd_received_total": received_amount if currency_code == "BDT" else Decimal("0"),
            "bd_unpaid_balance": unpaid_balance if currency_code == "BDT" else Decimal("0"),
            "production_received_rows": [],
        },
    )


@login_required
@user_passes_test(superuser_only)
def invoice_list_ca(request):
    return _invoice_list_by_currency(request, "CAD")


@login_required
@user_passes_test(superuser_only)
def invoice_list_bd(request):
    return _invoice_list_by_currency(request, "BDT")


@login_required
@user_passes_test(superuser_only)
def invoice_add(request):
    # optional prefill from order
    order_id = request.GET.get("order_id")
    initial = {}

    if order_id:
        try:
            order = ProductionOrder.objects.select_related("customer").get(pk=int(order_id))
            initial["order"] = order
            if order.customer_id:
                initial["customer"] = order.customer
        except Exception:
            order = None

    if request.method == "POST":
        form = InvoiceForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                inv = form.save(commit=False)

                # defaults
                if not inv.issue_date:
                    inv.issue_date = timezone.now().date()

                # invoice number
                if not (inv.invoice_number or "").strip():
                    inv.invoice_number = _next_invoice_number()

                # auto customer from order if missing
                if inv.order_id and not inv.customer_id:
                    try:
                        inv.customer_id = inv.order.customer_id
                    except Exception:
                        pass

                _calc_totals(inv)
                inv.save()
                form.save_m2m()

            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(initial=initial)

    return render(request, "crm/invoice/invoice_form.html", {"form": form, "mode": "add"})


@login_required
@user_passes_test(superuser_only)
def invoice_add_ca(request):
    # wrapper to force CAD
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                inv = form.save(commit=False)
                if not inv.currency:
                    inv.currency = "CAD"
                if not inv.issue_date:
                    inv.issue_date = timezone.now().date()
                if not (inv.invoice_number or "").strip():
                    inv.invoice_number = _next_invoice_number()
                if inv.order_id and not inv.customer_id:
                    try:
                        inv.customer_id = inv.order.customer_id
                    except Exception:
                        pass
                _calc_totals(inv)
                inv.save()
                form.save_m2m()
            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(initial={"currency": "CAD"})
    return render(request, "crm/invoice/invoice_form.html", {"form": form, "mode": "add"})


@login_required
@user_passes_test(superuser_only)
def invoice_add_bd(request):
    # wrapper to force BDT
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                inv = form.save(commit=False)
                if not inv.currency:
                    inv.currency = "BDT"
                if not inv.issue_date:
                    inv.issue_date = timezone.now().date()
                if not (inv.invoice_number or "").strip():
                    inv.invoice_number = _next_invoice_number()
                if inv.order_id and not inv.customer_id:
                    try:
                        inv.customer_id = inv.order.customer_id
                    except Exception:
                        pass
                _calc_totals(inv)
                inv.save()
                form.save_m2m()
            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(initial={"currency": "BDT"})
    return render(request, "crm/invoice/invoice_form.html", {"form": form, "mode": "add"})


@login_required
@user_passes_test(superuser_only)
def invoice_edit(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)

    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=inv)
        if form.is_valid():
            with transaction.atomic():
                inv2 = form.save(commit=False)

                if not (inv2.invoice_number or "").strip():
                    inv2.invoice_number = _next_invoice_number()

                if inv2.order_id and not inv2.customer_id:
                    try:
                        inv2.customer_id = inv2.order.customer_id
                    except Exception:
                        pass

                _calc_totals(inv2)
                inv2.save()
                form.save_m2m()

            messages.success(request, "Invoice updated.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(instance=inv)

    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "edit", "invoice": inv},
    )


@login_required
@user_passes_test(superuser_only)
def invoice_view(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("order", "customer"), pk=pk)
    payment_history = list(
        inv.payments.select_related("production_order", "accounting_entry", "created_by").order_by("-payment_date", "-id")
    )
    payment_total = sum((_d(payment.amount) for payment in payment_history), Decimal("0"))
    legacy_paid_amount = _d(inv.paid_amount) - payment_total
    if legacy_paid_amount < 0:
        legacy_paid_amount = Decimal("0")

    initial = {
        "payment_date": timezone.localdate(),
        "currency": inv.currency or "CAD",
        "side": _invoice_payment_side(inv),
        "production_order": inv.order_id or None,
    }
    initial.update(_payment_rate_initial(inv.currency))

    return render(
        request,
        "crm/invoice/invoice_view.html",
        {
            "invoice": inv,
            "payment_form": InvoicePaymentForm(invoice=inv, initial=initial),
            "payment_history": payment_history,
            "payment_total": payment_total,
            "legacy_paid_amount": legacy_paid_amount,
            "is_payment_month_closed": _is_accounting_month_closed(timezone.localdate(), _invoice_payment_side(inv)),
        },
    )


@login_required
@user_passes_test(superuser_only)
@require_POST
def invoice_payment_add(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("order", "customer"), pk=pk)
    form = InvoicePaymentForm(request.POST, invoice=inv)

    if not form.is_valid():
        payment_history = list(
            inv.payments.select_related("production_order", "accounting_entry", "created_by").order_by("-payment_date", "-id")
        )
        payment_total = sum((_d(payment.amount) for payment in payment_history), Decimal("0"))
        legacy_paid_amount = _d(inv.paid_amount) - payment_total
        if legacy_paid_amount < 0:
            legacy_paid_amount = Decimal("0")
        messages.error(request, "Could not save payment. Please fix the errors below.")
        return render(
            request,
            "crm/invoice/invoice_view.html",
            {
                "invoice": inv,
                "payment_form": form,
                "payment_history": payment_history,
                "payment_total": payment_total,
                "legacy_paid_amount": legacy_paid_amount,
                "is_payment_month_closed": _is_accounting_month_closed(timezone.localdate(), _invoice_payment_side(inv)),
            },
        )

    payment_date = form.cleaned_data["payment_date"]
    side = (form.cleaned_data.get("side") or _invoice_payment_side(inv)).upper().strip()

    if _is_accounting_month_closed(payment_date, side):
        form.add_error(
            "payment_date",
            f"{side} accounting is closed for {payment_date:%Y-%m}. Open the month before recording this payment.",
        )
        messages.error(request, "Payment blocked because the accounting month is closed.")
        payment_history = list(
            inv.payments.select_related("production_order", "accounting_entry", "created_by").order_by("-payment_date", "-id")
        )
        payment_total = sum((_d(payment.amount) for payment in payment_history), Decimal("0"))
        legacy_paid_amount = _d(inv.paid_amount) - payment_total
        if legacy_paid_amount < 0:
            legacy_paid_amount = Decimal("0")
        return render(
            request,
            "crm/invoice/invoice_view.html",
            {
                "invoice": inv,
                "payment_form": form,
                "payment_history": payment_history,
                "payment_total": payment_total,
                "legacy_paid_amount": legacy_paid_amount,
                "is_payment_month_closed": True,
            },
        )

    with transaction.atomic():
        payment = form.save(commit=False)
        payment.invoice = inv
        payment.side = side
        payment.created_by = request.user if request.user.is_authenticated else None
        if not payment.production_order_id and inv.order_id:
            payment.production_order = inv.order
        payment.save()

        entry = AccountingEntry.objects.create(
            date=payment.payment_date,
            side=payment.side,
            direction=AccountingEntry.DIR_IN,
            status="PAID",
            main_type="INCOME",
            sub_type="Invoice payment received",
            customer=inv.customer,
            production_order=payment.production_order,
            currency=payment.currency,
            amount_original=payment.amount,
            rate_to_cad=payment.rate_to_cad,
            rate_to_bdt=payment.rate_to_bdt,
            description=f"Payment received for invoice {inv.invoice_number}",
            internal_note=payment.notes or "",
            created_by=request.user if request.user.is_authenticated else None,
        )
        _audit_accounting_entry(entry, request.user, note=f"Invoice payment {inv.invoice_number}")

        payment.accounting_entry = entry
        payment.save(update_fields=["accounting_entry"])

        inv.paid_amount = _d(inv.paid_amount) + _d(payment.amount)
        _sync_invoice_payment_status(inv)
        inv.updated_at = timezone.now()
        inv.save(update_fields=["paid_amount", "status", "updated_at"])

    if inv.payment_status_key == "overpaid":
        messages.warning(request, "Payment saved. This invoice is now overpaid; review the balance.")
    else:
        messages.success(request, "Payment received and accounting entry saved.")
    return redirect("invoice_view", pk=inv.pk)


@login_required
@user_passes_test(superuser_only)
@require_POST
def invoice_approve(request, pk):
    """
    Lightweight approve endpoint to avoid 500s if approval fields are not present.
    Marks invoice as sent and sets approved fields if they exist.
    """
    inv = get_object_or_404(Invoice, pk=pk)
    if hasattr(inv, "approved_at"):
        inv.approved_at = timezone.now()
    if hasattr(inv, "approved_by"):
        inv.approved_by = request.user
    if hasattr(inv, "status"):
        inv.status = "sent"
    inv.save()
    messages.success(request, "Invoice approved.")
    return redirect("invoice_view", pk=inv.pk)
