# crm/views_invoice.py

import io
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.staticfiles import finders
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    AccountingEntry,
    AccountingEntryAudit,
    AccountingMonthClose,
    AccountingMonthLock,
    Customer,
    ExchangeRate,
    Invoice,
    InvoicePayment,
    ProductionOrder,
)
from .forms import InvoiceForm, InvoicePaymentForm
from .permissions import can_view_internal_costing
from .services.costing_workflow import CostingWorkflowError, create_or_link_production_order_from_invoice
from .services.order_lifecycle import build_lifecycle_profit_breakdown, create_lifecycle_from_invoice
from .services.workflow_visibility import build_workflow_visibility_context


DEFAULT_INVOICE_TERMS = """For bulk orders, 50% advance confirms the order and 50% is due before shipment.

For samples, 100% payment is required before development begins.

Production starts after payment is cleared.

Any change after approval may affect price and timeline.

Shipping time may vary due to courier, customs, or international delay.

Import duties and local taxes are the buyer's responsibility unless agreed otherwise.

Any issue must be reported within 5 days of receiving goods.

All agreements are governed under the laws of British Columbia, Canada."""


def can_manage_invoices(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.is_staff:
        return True
    try:
        access = user.access
    except Exception:
        return False
    return bool(getattr(access, "can_accounting_ca", False) or getattr(access, "can_accounting_bd", False))


def superuser_only(user):
    return can_manage_invoices(user)


def can_manage_invoice_internal_costing(user):
    return can_view_internal_costing(user)


def _sanitize_invoice_internal_fields(inv: Invoice) -> Invoice:
    inv.sewing_charge = Decimal("0")
    inv.other_internal_cost = Decimal("0")
    inv.internal_cost_note = ""
    return inv


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


def _parse_ar_date(value):
    value = (value or "").strip()
    return parse_date(value) if value else None


def _apply_ar_invoice_filters(invoices, filters):
    if filters["date_from"]:
        invoices = invoices.filter(issue_date__gte=filters["date_from"])
    if filters["date_to"]:
        invoices = invoices.filter(issue_date__lte=filters["date_to"])
    if filters["customer_id"]:
        invoices = invoices.filter(customer_id=filters["customer_id"])
    if filters["currency"]:
        invoices = invoices.filter(currency=filters["currency"])
    if filters["production_linked"]:
        invoices = invoices.filter(order__isnull=False)

    side = filters["side"]
    if side == "BD":
        invoices = invoices.filter(Q(invoice_region="BD") | Q(currency="BDT"))
    elif side == "CA":
        invoices = invoices.filter(
            Q(invoice_region="CA")
            | (Q(invoice_region="") & Q(currency__in=["CAD", "USD"]))
        )

    status = filters["status"]
    today = timezone.localdate()
    if status == "unpaid":
        invoices = invoices.filter(paid_amount__lte=0).exclude(status="cancelled")
    elif status == "overdue":
        invoices = invoices.filter(due_date__lt=today, total_amount__gt=F("paid_amount")).exclude(status="cancelled")
    elif status == "overpaid":
        invoices = invoices.filter(total_amount__gt=0, paid_amount__gt=F("total_amount"))
    elif status in {choice[0] for choice in Invoice.STATUS_CHOICES}:
        invoices = invoices.filter(status=status)

    return invoices


def _apply_ar_payment_filters(payments, filters):
    if filters["date_from"]:
        payments = payments.filter(payment_date__gte=filters["date_from"])
    if filters["date_to"]:
        payments = payments.filter(payment_date__lte=filters["date_to"])
    if filters["customer_id"]:
        payments = payments.filter(invoice__customer_id=filters["customer_id"])
    if filters["currency"]:
        payments = payments.filter(currency=filters["currency"])
    if filters["side"]:
        payments = payments.filter(side=filters["side"])
    if filters["production_linked"]:
        payments = payments.filter(production_order__isnull=False)

    status = filters["status"]
    today = timezone.localdate()
    if status == "unpaid":
        payments = payments.none()
    elif status == "overdue":
        payments = payments.filter(
            invoice__due_date__lt=today,
            invoice__total_amount__gt=F("invoice__paid_amount"),
        ).exclude(invoice__status="cancelled")
    elif status == "overpaid":
        payments = payments.filter(
            invoice__total_amount__gt=0,
            invoice__paid_amount__gt=F("invoice__total_amount"),
        )
    elif status in {choice[0] for choice in Invoice.STATUS_CHOICES}:
        payments = payments.filter(invoice__status=status)

    return payments


def _ar_currency_totals(rows, value_func):
    totals = {}
    for row in rows:
        currency = (getattr(row, "currency", "") or "Unknown").upper()
        totals[currency] = totals.get(currency, Decimal("0")) + _d(value_func(row))
    return [
        {"currency": currency, "amount": amount}
        for currency, amount in sorted(totals.items())
        if amount != 0
    ]


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
    region = (getattr(inv, "invoice_region", "") or "").upper().strip()
    if region in {"CA", "BD"}:
        return region

    customer = getattr(inv, "customer", None)
    country = (getattr(customer, "country", "") or "").lower().strip() if customer else ""
    if country in {"bd", "bangladesh"} or "bangladesh" in country:
        return "BD"
    if country in {"ca", "canada"} or "canada" in country:
        return "CA"

    return "BD" if (getattr(inv, "currency", "") or "").upper().strip() == "BDT" else "CA"


def _invoice_company(region: str) -> dict:
    region = "BD" if region == "BD" else "CA"
    return {
        "name": getattr(settings, "INVOICE_COMPANY_NAME", "Iconic Apparel House"),
        "email": getattr(settings, "INVOICE_COMPANY_EMAIL", "info@iconicapparelhouse.com"),
        "phone": getattr(settings, "INVOICE_COMPANY_PHONE", "604-500-6009"),
        "website": getattr(settings, "INVOICE_COMPANY_WEBSITE", "iconicapparelhouse.com"),
        "logo_path": getattr(settings, "INVOICE_LOGO_PATH", "img/image.png"),
        "office_label": "Bangladesh" if region == "BD" else "Canada",
        "address": getattr(settings, f"INVOICE_ADDRESS_{region}", ""),
        "tax_label": getattr(settings, f"INVOICE_TAX_LABEL_{region}", ""),
        "tax_id": getattr(settings, f"INVOICE_TAX_ID_{region}", ""),
    }


def _invoice_policy_text(inv: Invoice) -> str:
    override = (getattr(inv, "terms_override", "") or "").strip()
    return override or DEFAULT_INVOICE_TERMS


def _invoice_payment_status(inv: Invoice) -> dict:
    key = getattr(inv, "payment_status_key", "unpaid")
    notes = {
        "unpaid": "Payment has not been received yet.",
        "partial": "Partial payment has been received.",
        "paid": "Payment is complete.",
        "overpaid": "Received amount is higher than the invoice total.",
    }
    return {
        "key": key,
        "label": getattr(inv, "payment_status_label", "Unpaid"),
        "note": notes.get(key, "Payment status is being reviewed."),
        "paid": _d(getattr(inv, "paid_amount", Decimal("0"))),
        "balance": _d(getattr(inv, "balance", Decimal("0"))),
    }


def _invoice_line_items(inv: Invoice) -> list[dict]:
    order = getattr(inv, "order", None)
    subtotal = _d(getattr(inv, "subtotal", Decimal("0")))
    qty = _d(getattr(order, "qty_total", Decimal("0"))) if order else Decimal("0")
    rate = Decimal("0")
    if qty > 0 and subtotal > 0:
        rate = (subtotal / qty).quantize(Decimal("0.01"))

    description = "Apparel production"
    detail_parts = []
    if order:
        description = getattr(order, "title", "") or getattr(order, "style_name", "") or getattr(order, "order_code", "") or description
        if getattr(order, "style_name", ""):
            detail_parts.append(f"Style: {order.style_name}")
        if getattr(order, "color_info", ""):
            detail_parts.append(f"Color: {order.color_info}")
        if getattr(order, "order_code", ""):
            detail_parts.append(f"Order code: {order.order_code}")

    rows = [
        {
            "description": description,
            "qty": qty,
            "rate": rate,
            "amount": subtotal,
            "has_qty": qty > 0,
            "has_rate": rate > 0,
            "has_amount": subtotal > 0,
            "is_detail": False,
        }
    ]
    if detail_parts:
        rows.append(
            {
                "description": " | ".join(detail_parts),
                "qty": Decimal("0"),
                "rate": Decimal("0"),
                "amount": Decimal("0"),
                "has_qty": False,
                "has_rate": False,
                "has_amount": False,
                "is_detail": True,
            }
        )
    return rows


def _invoice_client_context(inv: Invoice, user=None) -> dict:
    inv = _sanitize_invoice_internal_fields(inv)
    region = _invoice_region(inv)
    return {
        "invoice": inv,
        "company": _invoice_company(region),
        "line_items": _invoice_line_items(inv),
        "payment_status": _invoice_payment_status(inv),
        "policy_text": _invoice_policy_text(inv),
        "can_approve_invoice": can_manage_invoices(user),
    }


def _invoice_client_template(inv: Invoice) -> str:
    return "crm/invoice/invoice_bd.html" if _invoice_region(inv) == "BD" else "crm/invoice/invoice_ca.html"


@login_required
@user_passes_test(superuser_only)
def accounts_receivable_dashboard(request):
    filters = {
        "date_from": _parse_ar_date(request.GET.get("date_from")),
        "date_to": _parse_ar_date(request.GET.get("date_to")),
        "customer_id": (request.GET.get("customer") or "").strip(),
        "status": (request.GET.get("status") or "").strip(),
        "currency": (request.GET.get("currency") or "").strip().upper(),
        "side": (request.GET.get("side") or "").strip().upper(),
        "production_linked": (request.GET.get("production_linked") or "") == "1",
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "customer": filters["customer_id"],
        "status": filters["status"],
        "currency": filters["currency"],
        "side": filters["side"],
        "production_linked": filters["production_linked"],
    }

    invoices_qs = Invoice.objects.select_related("order", "customer")
    invoices_qs = _apply_ar_invoice_filters(invoices_qs, filters)
    invoice_rows = list(invoices_qs.order_by("due_date", "-issue_date", "-created_at"))

    payments_qs = InvoicePayment.objects.select_related(
        "invoice",
        "invoice__customer",
        "production_order",
        "accounting_entry",
    )
    payments_qs = _apply_ar_payment_filters(payments_qs, filters)
    payment_rows = list(payments_qs.order_by("-payment_date", "-id"))

    today = timezone.localdate()
    open_invoices = [inv for inv in invoice_rows if _d(inv.balance) > 0 and inv.status != "cancelled"]
    overdue_invoices = [inv for inv in open_invoices if inv.due_date and inv.due_date < today]
    partial_invoices = [inv for inv in invoice_rows if inv.payment_status_key == "partial"]
    paid_invoices = [inv for inv in invoice_rows if inv.payment_status_key == "paid"]

    total_invoiced = sum((_d(inv.total_amount) for inv in invoice_rows), Decimal("0"))
    total_received = sum((_d(inv.paid_amount) for inv in invoice_rows), Decimal("0"))
    total_balance_due = sum((_d(inv.balance) for inv in open_invoices), Decimal("0"))
    visible_payment_total = sum((_d(payment.amount) for payment in payment_rows), Decimal("0"))

    invoiced_by_currency = _ar_currency_totals(invoice_rows, lambda inv: inv.total_amount)
    received_by_currency = _ar_currency_totals(invoice_rows, lambda inv: inv.paid_amount)
    balance_by_currency = _ar_currency_totals(open_invoices, lambda inv: inv.balance)
    payment_history_by_currency = _ar_currency_totals(payment_rows, lambda payment: payment.amount)

    outstanding_rows = []
    for inv in open_invoices[:75]:
        outstanding_rows.append(
            {
                "invoice": inv,
                "balance": _d(inv.balance),
                "is_overdue": bool(inv.due_date and inv.due_date < today),
                "days_overdue": (today - inv.due_date).days if inv.due_date and inv.due_date < today else 0,
            }
        )

    bd_payment_rows = [
        payment
        for payment in payment_rows
        if payment.side == "BD" and payment.production_order_id
    ]
    bd_production_received_total = sum((_d(payment.amount_bdt) for payment in bd_payment_rows), Decimal("0"))
    bd_receipts_by_order = {}
    for payment in bd_payment_rows:
        order = payment.production_order
        key = payment.production_order_id
        if key not in bd_receipts_by_order:
            bd_receipts_by_order[key] = {
                "order": order,
                "received_bdt": Decimal("0"),
                "received_original": Decimal("0"),
                "payments": 0,
            }
        bd_receipts_by_order[key]["received_bdt"] += _d(payment.amount_bdt)
        bd_receipts_by_order[key]["received_original"] += _d(payment.amount)
        bd_receipts_by_order[key]["payments"] += 1
    bd_receipt_rows = sorted(
        bd_receipts_by_order.values(),
        key=lambda row: row["received_bdt"],
        reverse=True,
    )[:20]

    customer_balances = {}
    for inv in open_invoices:
        customer = inv.customer
        name = customer.account_brand or customer.contact_name if customer else "No customer"
        key = (customer.pk if customer else 0, name, inv.currency or "Unknown")
        if key not in customer_balances:
            customer_balances[key] = {
                "customer": customer,
                "name": name,
                "currency": inv.currency or "Unknown",
                "invoice_count": 0,
                "overdue_count": 0,
                "invoiced": Decimal("0"),
                "received": Decimal("0"),
                "balance": Decimal("0"),
            }
        row = customer_balances[key]
        row["invoice_count"] += 1
        row["overdue_count"] += 1 if inv.due_date and inv.due_date < today else 0
        row["invoiced"] += _d(inv.total_amount)
        row["received"] += _d(inv.paid_amount)
        row["balance"] += _d(inv.balance)
    customer_balance_rows = sorted(
        customer_balances.values(),
        key=lambda row: row["balance"],
        reverse=True,
    )[:25]

    monthly_map = {}
    for payment in payment_rows:
        if not payment.payment_date:
            continue
        key = payment.payment_date.strftime("%Y-%m")
        if key not in monthly_map:
            monthly_map[key] = {
                "key": key,
                "label": payment.payment_date.strftime("%b %Y"),
                "received": Decimal("0"),
                "received_bdt": Decimal("0"),
                "payments": 0,
            }
        monthly_map[key]["received"] += _d(payment.amount)
        monthly_map[key]["received_bdt"] += _d(payment.amount_bdt)
        monthly_map[key]["payments"] += 1
    monthly_rows = [monthly_map[key] for key in sorted(monthly_map.keys())]
    max_monthly = max([row["received"] for row in monthly_rows] or [Decimal("0")])
    for row in monthly_rows:
        row["bar_percent"] = int((row["received"] / max_monthly) * 100) if max_monthly > 0 else 0

    customers = Customer.objects.filter(invoice__isnull=False).distinct().order_by("account_brand", "contact_name")

    return render(
        request,
        "crm/invoice/accounts_receivable_dashboard.html",
        {
            "filter_values": filter_values,
            "customers": customers,
            "status_options": [
                ("", "All statuses"),
                ("unpaid", "Unpaid"),
                ("partial", "Partially paid"),
                ("paid", "Paid"),
                ("overdue", "Overdue"),
                ("overpaid", "Overpaid"),
                ("draft", "Draft"),
                ("sent", "Sent"),
                ("cancelled", "Cancelled"),
            ],
            "currency_options": ["USD", "CAD", "BDT"],
            "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
            "total_invoiced": total_invoiced,
            "total_received": total_received,
            "total_balance_due": total_balance_due,
            "visible_payment_total": visible_payment_total,
            "invoiced_by_currency": invoiced_by_currency,
            "received_by_currency": received_by_currency,
            "balance_by_currency": balance_by_currency,
            "payment_history_by_currency": payment_history_by_currency,
            "overdue_count": len(overdue_invoices),
            "partial_count": len(partial_invoices),
            "paid_count": len(paid_invoices),
            "invoice_count": len(invoice_rows),
            "payment_count": len(payment_rows),
            "bd_production_received_total": bd_production_received_total,
            "outstanding_rows": outstanding_rows,
            "payment_rows": payment_rows[:75],
            "bd_receipt_rows": bd_receipt_rows,
            "customer_balance_rows": customer_balance_rows,
            "monthly_rows": monthly_rows[-12:],
        },
    )


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
    can_edit_internal_costs = can_manage_invoice_internal_costing(request.user)

    if order_id:
        try:
            order = ProductionOrder.objects.select_related("customer").get(pk=int(order_id))
            initial["order"] = order
            if order.customer_id:
                initial["customer"] = order.customer
        except Exception:
            order = None

    if request.method == "POST":
        form = InvoiceForm(request.POST, can_edit_internal_costs=can_edit_internal_costs)
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
                create_lifecycle_from_invoice(inv, user=request.user)

            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(initial=initial, can_edit_internal_costs=can_edit_internal_costs)

    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "add", "can_manage_invoice_costing": can_edit_internal_costs},
    )


@login_required
@user_passes_test(superuser_only)
def invoice_add_ca(request):
    # wrapper to force CAD
    can_edit_internal_costs = can_manage_invoice_internal_costing(request.user)
    if request.method == "POST":
        form = InvoiceForm(request.POST, can_edit_internal_costs=can_edit_internal_costs)
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
        form = InvoiceForm(initial={"currency": "CAD"}, can_edit_internal_costs=can_edit_internal_costs)
    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "add", "can_manage_invoice_costing": can_edit_internal_costs},
    )


@login_required
@user_passes_test(superuser_only)
def invoice_add_bd(request):
    # wrapper to force BDT
    can_edit_internal_costs = can_manage_invoice_internal_costing(request.user)
    if request.method == "POST":
        form = InvoiceForm(request.POST, can_edit_internal_costs=can_edit_internal_costs)
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
        form = InvoiceForm(initial={"currency": "BDT"}, can_edit_internal_costs=can_edit_internal_costs)
    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "add", "can_manage_invoice_costing": can_edit_internal_costs},
    )


@login_required
@user_passes_test(superuser_only)
def invoice_edit(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    can_edit_internal_costs = can_manage_invoice_internal_costing(request.user)

    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=inv, can_edit_internal_costs=can_edit_internal_costs)
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
                create_lifecycle_from_invoice(inv2, user=request.user)

            messages.success(request, "Invoice updated.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(instance=inv, can_edit_internal_costs=can_edit_internal_costs)

    display_invoice = inv if can_edit_internal_costs else _sanitize_invoice_internal_fields(inv)
    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "edit", "invoice": display_invoice, "can_manage_invoice_costing": can_edit_internal_costs},
    )


@login_required
@user_passes_test(superuser_only)
def invoice_view(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("order", "customer", "costing_header"), pk=pk)
    payment_history = list(
        inv.payments.select_related("production_order", "accounting_entry", "created_by").order_by("-payment_date", "-id")
    )
    payment_total = sum((_d(payment.amount) for payment in payment_history), Decimal("0"))
    legacy_paid_amount = _d(inv.paid_amount) - payment_total
    if legacy_paid_amount < 0:
        legacy_paid_amount = Decimal("0")
    lifecycle = inv.order_lifecycles.order_by("-updated_at", "-id").first()
    can_view_invoice_costing = can_manage_invoice_internal_costing(request.user)
    lifecycle_profit = None
    if lifecycle and can_view_invoice_costing:
        lifecycle_profit = build_lifecycle_profit_breakdown(lifecycle)
    display_invoice = inv if can_view_invoice_costing else _sanitize_invoice_internal_fields(inv)
    workflow_visibility = build_workflow_visibility_context(
        "invoice",
        user=request.user,
        invoice=inv,
        costing=getattr(inv, "costing_header", None),
        production_order=getattr(inv, "order", None),
        lifecycle=lifecycle,
    )

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
            "invoice": display_invoice,
            "payment_form": InvoicePaymentForm(invoice=inv, initial=initial),
            "payment_history": payment_history,
            "payment_total": payment_total,
            "legacy_paid_amount": legacy_paid_amount,
            "is_payment_month_closed": _is_accounting_month_closed(timezone.localdate(), _invoice_payment_side(inv)),
            "can_manage_invoice_costing": can_view_invoice_costing,
            "lifecycle": lifecycle,
            "lifecycle_profit": lifecycle_profit,
            **workflow_visibility,
        },
    )


@login_required
@user_passes_test(superuser_only)
def invoice_client_view(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("order", "customer"), pk=pk)
    return render(request, _invoice_client_template(inv), _invoice_client_context(inv, request.user))


def _pdf_money(currency: str, amount) -> str:
    return f"{currency} {_d(amount):,.2f}" if currency else f"{_d(amount):,.2f}"


def _pdf_text_lines(pdf, text: str, max_width: int, font_name: str, font_size: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]

    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdf.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@login_required
@user_passes_test(superuser_only)
def invoice_pdf(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("order", "customer"), pk=pk)

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError:
        messages.error(request, "PDF export is unavailable. Please install ReportLab.")
        return redirect("invoice_client_view", pk=pk)

    context = _invoice_client_context(inv, request.user)
    company = context["company"]
    payment_status = context["payment_status"]
    line_items = context["line_items"]
    currency = getattr(inv, "currency", "") or ""

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, pageCompression=0)
    width, height = letter
    left = 46
    right = width - 46
    y = height - 44

    def ensure_space(required):
        nonlocal y
        if y < required:
            pdf.showPage()
            y = height - 46

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.rect(0, height - 104, width, 104, fill=1, stroke=0)

    logo_path = company.get("logo_path") or ""
    logo_file = finders.find(logo_path) if logo_path else ""
    if logo_file:
        try:
            pdf.drawImage(ImageReader(logo_file), left, height - 92, width=54, height=54, preserveAspectRatio=True, mask="auto")
        except Exception:
            logo_file = ""

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(left + 66 if logo_file else left, height - 60, company["name"])
    pdf.setFont("Helvetica", 9)
    meta_start = left + 66 if logo_file else left
    pdf.drawString(meta_start, height - 76, "Professional apparel manufacturing invoice")

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawRightString(right, height - 58, "INVOICE")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(right, height - 76, f"Invoice # {inv.invoice_number or ''}")

    y = height - 132
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "From")
    pdf.drawString(width / 2 + 12, y, "Invoice Details")
    y -= 15

    pdf.setFont("Helvetica", 9)
    company_lines = [
        company.get("name", ""),
        company.get("address", ""),
        " | ".join(part for part in [company.get("phone", ""), company.get("email", "")] if part),
        company.get("website", ""),
    ]
    detail_lines = [
        f"Issue date: {inv.issue_date:%Y-%m-%d}" if inv.issue_date else "Issue date: -",
        f"Due date: {inv.due_date:%Y-%m-%d}" if inv.due_date else "Due date: -",
        f"Currency: {currency or '-'}",
        f"Payment status: {payment_status['label']}",
    ]
    row_y = y
    for line in [line for line in company_lines if line]:
        pdf.drawString(left, row_y, line[:84])
        row_y -= 12
    row_y = y
    for line in detail_lines:
        pdf.drawString(width / 2 + 12, row_y, line)
        row_y -= 12
    y -= 70

    ensure_space(120)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Bill To")
    y -= 15
    pdf.setFont("Helvetica", 9)
    customer = inv.customer
    customer_lines = []
    if customer:
        customer_lines = [
            getattr(customer, "account_brand", "") or getattr(customer, "contact_name", "") or "Customer",
            getattr(customer, "contact_name", ""),
            getattr(customer, "email", ""),
            getattr(customer, "phone", ""),
            getattr(customer, "address_line1", ""),
            getattr(customer, "address_line2", ""),
            " ".join(
                part
                for part in [
                    getattr(customer, "city", ""),
                    getattr(customer, "state", "") or getattr(customer, "province", ""),
                    getattr(customer, "postal_code", ""),
                    getattr(customer, "country", ""),
                ]
                if part
            ),
        ]
    else:
        customer_lines = ["Customer not set."]
    for line in [line for line in customer_lines if line]:
        pdf.drawString(left, y, line[:100])
        y -= 12

    y -= 14
    ensure_space(180)
    pdf.setFillColor(colors.HexColor("#f8fafc"))
    pdf.rect(left, y - 22, right - left, 24, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left + 8, y - 14, "Description")
    pdf.drawRightString(right - 198, y - 14, "Qty")
    pdf.drawRightString(right - 112, y - 14, "Rate")
    pdf.drawRightString(right - 8, y - 14, "Amount")
    y -= 30

    pdf.setFont("Helvetica", 9)
    for item in line_items:
        ensure_space(110)
        if item.get("is_detail"):
            pdf.setFillColor(colors.HexColor("#64748b"))
            pdf.setFont("Helvetica", 8)
        else:
            pdf.setFillColor(colors.HexColor("#111827"))
            pdf.setFont("Helvetica", 9)
        desc_lines = _pdf_text_lines(pdf, item["description"], 255, pdf._fontname, pdf._fontsize)
        start_y = y
        for desc_line in desc_lines:
            pdf.drawString(left + 8, y, desc_line)
            y -= 11
        if not item.get("is_detail"):
            pdf.drawRightString(right - 198, start_y, f"{item['qty']:,.0f}" if item.get("has_qty") else "-")
            pdf.drawRightString(right - 112, start_y, _pdf_money(currency, item["rate"]) if item.get("has_rate") else "-")
            pdf.drawRightString(right - 8, start_y, _pdf_money(currency, item["amount"]) if item.get("has_amount") else "-")
        pdf.setStrokeColor(colors.HexColor("#e2e8f0"))
        pdf.line(left, y - 2, right, y - 2)
        y -= 8

    y -= 8
    summary_x = right - 210
    summary_rows = [
        ("Subtotal", inv.subtotal),
        ("Shipping", inv.shipping_amount),
        ("Discount", inv.discount_amount),
        ("Tax", inv.tax_amount),
        ("Grand total", inv.total_amount),
        ("Amount paid", inv.paid_amount),
        ("Balance due", inv.balance),
    ]
    for label, amount in summary_rows:
        ensure_space(80)
        is_total = label in {"Grand total", "Balance due"}
        pdf.setFont("Helvetica-Bold" if is_total else "Helvetica", 10 if is_total else 9)
        pdf.drawString(summary_x, y, label)
        pdf.drawRightString(right, y, _pdf_money(currency, amount))
        y -= 15

    y -= 8
    ensure_space(130)
    pdf.setFillColor(colors.HexColor("#fef3c7"))
    pdf.roundRect(left, y - 28, right - left, 34, 7, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left + 12, y - 8, f"Payment status: {payment_status['label']}")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(left + 12, y - 21, payment_status["note"][:110])
    y -= 56

    if inv.notes:
        ensure_space(100)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left, y, "Notes")
        y -= 14
        pdf.setFont("Helvetica", 9)
        for line in _pdf_text_lines(pdf, inv.notes, int(right - left), "Helvetica", 9):
            ensure_space(60)
            pdf.drawString(left, y, line)
            y -= 12
        y -= 10

    ensure_space(160)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Terms and Conditions")
    y -= 14
    pdf.setFont("Helvetica", 8)
    for paragraph in (context["policy_text"] or "").splitlines():
        if not paragraph.strip():
            y -= 5
            continue
        for line in _pdf_text_lines(pdf, paragraph.strip(), int(right - left), "Helvetica", 8):
            ensure_space(50)
            pdf.drawString(left, y, line)
            y -= 11

    pdf.showPage()
    pdf.save()

    filename = f"invoice_{inv.invoice_number or inv.pk}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


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
        can_view_invoice_costing = can_manage_invoice_internal_costing(request.user)
        display_invoice = inv if can_view_invoice_costing else _sanitize_invoice_internal_fields(inv)
        messages.error(request, "Could not save payment. Please fix the errors below.")
        return render(
            request,
            "crm/invoice/invoice_view.html",
            {
                "invoice": display_invoice,
                "payment_form": form,
                "payment_history": payment_history,
                "payment_total": payment_total,
                "legacy_paid_amount": legacy_paid_amount,
                "is_payment_month_closed": _is_accounting_month_closed(timezone.localdate(), _invoice_payment_side(inv)),
                "can_manage_invoice_costing": can_view_invoice_costing,
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
        can_view_invoice_costing = can_manage_invoice_internal_costing(request.user)
        display_invoice = inv if can_view_invoice_costing else _sanitize_invoice_internal_fields(inv)
        return render(
            request,
            "crm/invoice/invoice_view.html",
            {
                "invoice": display_invoice,
                "payment_form": form,
                "payment_history": payment_history,
                "payment_total": payment_total,
                "legacy_paid_amount": legacy_paid_amount,
                "is_payment_month_closed": True,
                "can_manage_invoice_costing": can_view_invoice_costing,
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
        create_lifecycle_from_invoice(inv, user=request.user)

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
    create_lifecycle_from_invoice(inv, user=request.user)
    messages.success(request, "Invoice approved.")
    return redirect("invoice_view", pk=inv.pk)


@login_required
@user_passes_test(superuser_only)
@require_POST
def invoice_convert_to_production_order(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("costing_header", "order"), pk=pk)
    try:
        order, created = create_or_link_production_order_from_invoice(inv, user=request.user)
    except CostingWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("invoice_view", pk=pk)

    if created:
        messages.success(request, f"Production order {order.order_code or order.pk} created from invoice.")
    else:
        messages.info(request, f"Invoice linked to production order {order.order_code or order.pk}.")
    return redirect("production_detail", pk=order.pk)
