# crm/views_invoice.py

from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Invoice, InvoiceAudit, ProductionOrder
from .forms import InvoiceForm
from .permissions import get_access


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


def _invoice_region(inv: Invoice) -> str:
    region = (inv.invoice_region or "").upper().strip()
    if region in {"CA", "BD"}:
        return region
    return inv.infer_invoice_region()


def _company_profile(region: str) -> dict:
    name = getattr(settings, "INVOICE_COMPANY_NAME", "Iconic Apparel House")
    email = getattr(settings, "INVOICE_COMPANY_EMAIL", "info@iconicapparelhouse.com")
    phone = getattr(settings, "INVOICE_COMPANY_PHONE", "604-500-6009")
    website = getattr(settings, "INVOICE_COMPANY_WEBSITE", "iconicapparelhouse.com")
    logo_path = getattr(settings, "INVOICE_LOGO_PATH", "img/image.png")

    if region == "bd":
        office_label = "Bangladesh"
        address = getattr(settings, "INVOICE_ADDRESS_BD", "")
        tax_label = getattr(settings, "INVOICE_TAX_LABEL_BD", "VAT / BIN")
        tax_id = getattr(settings, "INVOICE_TAX_ID_BD", "")
    else:
        office_label = "Canada"
        address = getattr(settings, "INVOICE_ADDRESS_CA", "")
        tax_label = getattr(settings, "INVOICE_TAX_LABEL_CA", "GST / HST")
        tax_id = getattr(settings, "INVOICE_TAX_ID_CA", "")

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "website": website,
        "logo_path": logo_path,
        "office_label": office_label,
        "address": address,
        "tax_label": tax_label,
        "tax_id": tax_id,
    }


def _can_approve_invoice(user) -> bool:
    if user.is_superuser:
        return True
    access = get_access(user)
    return bool(access.can_accounting_ca or access.can_accounting_bd)


def _payment_status(inv: Invoice) -> dict:
    total = _d(inv.total_amount)
    paid = _d(inv.paid_amount)
    balance = total - paid
    if balance < 0:
        balance = Decimal("0")

    if total <= 0:
        label = "No total"
        note = "No total amount recorded."
    elif paid <= 0:
        label = "Unpaid"
        note = "No payment received yet."
    elif paid >= total:
        label = "Paid"
        note = "Paid in full."
    else:
        label = "Partial"
        note = "Partial payment received."

    return {
        "label": label,
        "note": note,
        "paid": paid,
        "balance": balance,
    }


def _policy_text(inv: Invoice) -> str:
    if (inv.terms_override or "").strip():
        return inv.terms_override

    deposit = inv.deposit_percent or Decimal("70")
    if deposit < 0:
        deposit = Decimal("0")
    if deposit > 100:
        deposit = Decimal("100")
    remaining = Decimal("100") - deposit

    def fmt_pct(val):
        raw = f"{val:.2f}"
        return raw.rstrip("0").rstrip(".")

    return "\n".join(
        [
            f"A {fmt_pct(deposit)} percent deposit is required to confirm your order.",
            f"The remaining {fmt_pct(remaining)} percent balance is due before shipment.",
            "Production timelines start after funds are cleared.",
            "Extra work or changes may result in additional charges.",
            "Delivery timelines may be affected by courier or customs delays.",
            "All prices are in CAD unless stated otherwise.",
        ]
    )


def _build_line_items(inv: Invoice):
    items = []
    subtotal = _d(inv.subtotal)

    if inv.order:
        title = inv.order.title or inv.order.order_code or "Production order"
        qty = getattr(inv.order, "qty_total", None)
        rate = None
        if qty:
            try:
                rate = (subtotal / Decimal(qty)).quantize(Decimal("0.01"))
            except Exception:
                rate = None
        items.append(
            {
                "description": title,
                "qty": qty,
                "rate": rate,
                "amount": subtotal,
                "is_detail": False,
                "has_qty": qty is not None,
                "has_rate": rate is not None,
                "has_amount": subtotal is not None,
            }
        )

        try:
            lines = list(inv.order.lines.all())
        except Exception:
            lines = []
        for line in lines:
            detail = " / ".join([v for v in [line.style_name, line.color_info] if v])
            if detail:
                items.append(
                    {
                        "description": detail,
                        "qty": None,
                        "rate": None,
                        "amount": None,
                        "is_detail": True,
                        "has_qty": False,
                        "has_rate": False,
                        "has_amount": False,
                    }
                )
    else:
        items.append(
            {
                "description": "Services and products",
                "qty": None,
                "rate": None,
                "amount": subtotal,
                "is_detail": False,
                "has_qty": False,
                "has_rate": False,
                "has_amount": subtotal is not None,
            }
        )

    return items


@login_required
def _invoice_list_base(request, region=None):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    currency = (request.GET.get("currency") or "").strip()

    invoices = Invoice.objects.select_related("order", "customer")

    if region in {"CA", "BD"}:
        invoices = invoices.filter(invoice_region=region)

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

    title = "Invoices"
    sub = "All invoices in the system. Use search and filter to find invoices fast."
    if region == "CA":
        title = "Canada invoices"
        sub = "Canada invoice module with payment terms and policy."
    elif region == "BD":
        title = "Bangladesh invoices"
        sub = "Bangladesh invoice module (billing only)."

    add_url = "invoice_add"
    if region == "CA":
        add_url = "invoice_add_ca"
    elif region == "BD":
        add_url = "invoice_add_bd"

    return render(
        request,
        "crm/invoice/invoice_list.html",
        {
            "invoices": invoices,
            "q": q,
            "status": status,
            "currency": currency,
            "module_title": title,
            "module_sub": sub,
            "add_url": add_url,
        },
    )


def invoice_list(request):
    return _invoice_list_base(request)


def invoice_list_ca(request):
    return _invoice_list_base(request, region="CA")


def invoice_list_bd(request):
    return _invoice_list_base(request, region="BD")


@login_required
def _invoice_add_base(request, region=None):
    # optional prefill from order
    order_id = request.GET.get("order_id")
    initial = {}
    if region in {"CA", "BD"}:
        initial["invoice_region"] = region

    if order_id:
        try:
            order = ProductionOrder.objects.select_related("customer").get(pk=int(order_id))
            initial["order"] = order
            if order.customer_id:
                initial["customer"] = order.customer
        except Exception:
            order = None

    if request.method == "POST":
        form = InvoiceForm(request.POST, user=request.user)
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

                if not (inv.invoice_region or "").strip() and region in {"CA", "BD"}:
                    inv.invoice_region = region

                _calc_totals(inv)
                inv.save()
                form.save_m2m()

            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(initial=initial, user=request.user)

    return render(request, "crm/invoice/invoice_form.html", {"form": form, "mode": "add"})


def invoice_add(request):
    return _invoice_add_base(request)


def invoice_add_ca(request):
    return _invoice_add_base(request, region="CA")


def invoice_add_bd(request):
    return _invoice_add_base(request, region="BD")


@login_required
def invoice_edit(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    if inv.invoice_status == "APPROVED":
        return HttpResponseForbidden("Invoice is approved and locked.")

    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=inv, user=request.user)
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
        form = InvoiceForm(instance=inv, user=request.user)

    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "edit", "invoice": inv},
    )


@login_required
def invoice_view(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("order", "customer"), pk=pk)
    region = _invoice_region(inv)
    company = _company_profile(region.lower())
    line_items = _build_line_items(inv)
    payment_status = _payment_status(inv)
    policy_text = _policy_text(inv) if region == "CA" else ""
    template_name = "crm/invoice/invoice_ca.html" if region == "CA" else "crm/invoice/invoice_bd.html"
    return render(
        request,
        template_name,
        {
            "invoice": inv,
            "invoice_region": region,
            "company": company,
            "line_items": line_items,
            "can_approve_invoice": _can_approve_invoice(request.user),
            "payment_status": payment_status,
            "policy_text": policy_text,
        },
    )


@login_required
def invoice_approve(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    inv = get_object_or_404(Invoice, pk=pk)
    if not _can_approve_invoice(request.user):
        return HttpResponseForbidden("No access")

    if inv.invoice_status == "APPROVED":
        messages.info(request, "Invoice is already approved.")
        return redirect("invoice_view", pk=inv.pk)

    inv.invoice_status = "APPROVED"
    inv.approved_at = timezone.now()
    inv.approved_by = request.user
    inv.save(update_fields=["invoice_status", "approved_at", "approved_by"])

    InvoiceAudit.objects.create(
        invoice=inv,
        action="approved",
        changed_by=request.user,
        note="Invoice approved",
    )

    messages.success(request, "Invoice approved and locked.")
    return redirect("invoice_view", pk=inv.pk)
