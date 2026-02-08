# crm/views_invoice.py

from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Invoice, ProductionOrder
from .forms import InvoiceForm


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
            | Q(customer__name__icontains=q)
        )

    if status:
        invoices = invoices.filter(status=status)

    if currency:
        invoices = invoices.filter(currency=currency)

    invoices = invoices.order_by("-issue_date", "-created_at")

    return render(
        request,
        "crm/invoice/invoice_list.html",
        {
            "invoices": invoices,
            "q": q,
            "status": status,
            "currency": currency,
        },
    )


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
    return render(request, "crm/invoice/invoice_view.html", {"invoice": inv})
