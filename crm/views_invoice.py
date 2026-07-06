# crm/views_invoice.py

import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.staticfiles import finders
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
    InvoiceSettings,
    ProductionOrder,
)
from .forms import InvoiceForm, InvoicePaymentForm, InvoiceSettingsForm
from .permissions import can_view_internal_costing, get_access
from .services.costing_workflow import CostingWorkflowError, create_or_link_production_order_from_invoice
from .services.order_lifecycle import build_lifecycle_profit_breakdown, create_lifecycle_from_invoice
from .services.workflow_visibility import build_workflow_visibility_context
from .services.operations_permissions import can_archive_invoices
from .services.local_sewing import calculate_local_sewing, is_bangladesh_local_sewing


DEFAULT_INVOICE_TERMS = """For bulk orders, 50% advance confirms the order and 50% is due before shipment.

For samples, 100% payment is required before development begins.

Production starts after payment is cleared.

Any change after approval may affect price and timeline.

Shipping time may vary due to courier, customs, or international delay.

Import duties and local taxes are the buyer's responsibility unless agreed otherwise.

Any issue must be reported within 5 days of receiving goods.

All agreements are governed under the laws of British Columbia, Canada."""

NORTH_AMERICA_INVOICE_TERMS = """Payment terms: sample invoices require 100% payment before development begins. Bulk production invoices require the stated deposit before production starts and the remaining balance before shipment.

Pricing and quotations: prices are valid only for the stated style, quantity, materials, and timeline.

Samples and approval: production begins after sample or artwork approval where applicable.

Production and lead time: lead times begin after payment, approvals, and required materials are complete.

Order changes and cancellation: approved production changes may affect price and timeline. Orders cannot be cancelled after production starts without written agreement.

Quality tolerance: standard apparel production tolerances apply for shade, sizing, trims, and quantity.

Shipping duties and risk: shipping, customs, duties, brokerage, and local taxes are the buyer's responsibility unless agreed in writing.

Intellectual property: the client confirms ownership or authorization to use supplied artwork, logos, and brand assets.

Claims and returns: claims must be submitted within 5 business days of delivery with supporting photos and documentation.

Limitation of liability: liability is limited to the invoiced value of the affected goods.

Governing law: all agreements are governed by the laws of British Columbia, Canada.

Client agreement: payment confirms acceptance of these invoice terms."""

BANGLADESH_INVOICE_TERMS = """Payment is required before work begins according to the stated advance or deposit terms.

Production begins after payment, approvals, and required materials are complete.

Standard apparel production tolerances apply for shade, sizing, trims, and quantity.

Orders cannot be cancelled after production starts without written agreement.

Claims must be submitted within 5 business days of delivery with supporting photos and documentation."""

INVOICE_MARKET_TO_REGION = {
    "north_america": "CA",
    "bangladesh": "BD",
}

INVOICE_REGION_TO_MARKET = {
    "CA": "north_america",
    "BD": "bangladesh",
}

INVOICE_LAYOUT_TITLES = {
    ("north_america", "sample"): "North America Sample Invoice",
    ("north_america", "bulk"): "North America Bulk Production Invoice",
    ("north_america", "sewing_charge"): "North America Bulk Production Invoice",
    ("bangladesh", "sample"): "Bangladesh Sample Invoice",
    ("bangladesh", "bulk"): "Bangladesh Bulk Production Invoice",
    ("bangladesh", "sewing_charge"): "Bangladesh Sewing Charge Invoice",
}


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


def _local_sewing_order(order_id):
    if not order_id:
        return None
    try:
        order = ProductionOrder.objects.prefetch_related("stages").get(pk=int(order_id))
    except (ProductionOrder.DoesNotExist, TypeError, ValueError):
        return None
    return order if is_bangladesh_local_sewing(order) else None


def _local_sewing_invoice_initial(order):
    summary = calculate_local_sewing(order)
    return {
        "order": order,
        "customer": order.customer,
        "currency": "BDT",
        "invoice_market": "bangladesh",
        "invoice_type": "sewing_charge",
        "subtotal": summary["total_sewing_revenue"],
        "shipping_amount": Decimal("0"),
        "deposit_percentage": _default_deposit_for("bangladesh", "sewing_charge"),
    }


def _apply_local_sewing_invoice_source(inv, order):
    summary = calculate_local_sewing(order)
    inv.order = order
    inv.quick_costing = order.source_quick_costing
    inv.customer = order.customer
    inv.currency = "BDT"
    inv.invoice_market = "bangladesh"
    inv.invoice_region = "BD"
    inv.invoice_type = "sewing_charge"
    inv.subtotal = summary["total_sewing_revenue"]
    inv.shipping_amount = Decimal("0")
    # Invoice.sewing_charge is an internal-cost field and is never a revenue source.
    return inv


def can_manage_invoice_settings(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        access = get_access(user)
    except Exception:
        return False
    return bool(getattr(access, "can_accounting_ca", False) or getattr(access, "can_accounting_bd", False))


def can_archive_invoice(user):
    return can_archive_invoices(user)


def _invoice_archive_scope(queryset, archive_filter="active"):
    if archive_filter == "all":
        return queryset
    return queryset.filter(is_archived=archive_filter == "archived")


def _invoice_payment_archive_scope(queryset, include_archived=False):
    return queryset if include_archived else queryset.filter(invoice__is_archived=False)


def _invoice_settings():
    return InvoiceSettings.active()


def _settings_file_url(settings_obj, field_name: str) -> str:
    if not settings_obj:
        return ""
    image = getattr(settings_obj, field_name, None)
    if not image:
        return ""
    try:
        return image.url
    except Exception:
        return ""


def _settings_file_path(settings_obj, field_name: str) -> str:
    if not settings_obj:
        return ""
    image = getattr(settings_obj, field_name, None)
    if not image:
        return ""
    try:
        return image.path
    except Exception:
        return ""


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
            data["rate_to_cad"] = cad_to_bdt

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


def _ar_aging_bucket(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1_30"
    if days_overdue <= 60:
        return "31_60"
    if days_overdue <= 90:
        return "61_90"
    return "90_plus"


def _ar_aging_rows(open_invoices, today):
    buckets = {
        "current": {"label": "Current", "invoice_count": 0, "total": Decimal("0"), "currency_map": {}, "tone": "good"},
        "1_30": {"label": "1-30 days", "invoice_count": 0, "total": Decimal("0"), "currency_map": {}, "tone": "warn"},
        "31_60": {"label": "31-60 days", "invoice_count": 0, "total": Decimal("0"), "currency_map": {}, "tone": "bad"},
        "61_90": {"label": "61-90 days", "invoice_count": 0, "total": Decimal("0"), "currency_map": {}, "tone": "bad"},
        "90_plus": {"label": "90+ days", "invoice_count": 0, "total": Decimal("0"), "currency_map": {}, "tone": "bad"},
    }
    order = ["current", "1_30", "31_60", "61_90", "90_plus"]

    for inv in open_invoices:
        balance = _d(inv.balance)
        if balance <= 0:
            continue
        days_overdue = (today - inv.due_date).days if inv.due_date and inv.due_date < today else 0
        bucket = buckets[_ar_aging_bucket(days_overdue)]
        currency = (inv.currency or "Unknown").upper()
        bucket["invoice_count"] += 1
        bucket["total"] += balance
        bucket["currency_map"][currency] = bucket["currency_map"].get(currency, Decimal("0")) + balance

    rows = []
    for key in order:
        row = buckets[key]
        row["currency_totals"] = [
            {"currency": currency, "amount": amount}
            for currency, amount in sorted(row["currency_map"].items())
            if amount != 0
        ]
        rows.append(row)
    return rows


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


def _invoice_market(inv: Invoice) -> str:
    region = (getattr(inv, "invoice_region", "") or "").upper().strip()
    currency = (getattr(inv, "currency", "") or "").upper().strip()
    if region == "BD" or currency == "BDT":
        return "bangladesh"
    if region == "CA":
        return "north_america"

    market = (getattr(inv, "invoice_market", "") or "").strip()
    if market in {"north_america", "bangladesh"}:
        return market

    return "north_america"


def _invoice_region(inv: Invoice) -> str:
    region = (getattr(inv, "invoice_region", "") or "").upper().strip()
    if region in {"CA", "BD"}:
        return region

    market = (getattr(inv, "invoice_market", "") or "").strip()
    if market in INVOICE_MARKET_TO_REGION:
        return INVOICE_MARKET_TO_REGION[market]

    customer = getattr(inv, "customer", None)
    country = (getattr(customer, "country", "") or "").lower().strip() if customer else ""
    if country in {"bd", "bangladesh"} or "bangladesh" in country:
        return "BD"
    if country in {"ca", "canada"} or "canada" in country:
        return "CA"

    return "BD" if (getattr(inv, "currency", "") or "").upper().strip() == "BDT" else "CA"


def _invoice_type(inv: Invoice) -> str:
    invoice_type = (getattr(inv, "invoice_type", "") or "").strip()
    return invoice_type if invoice_type in {"sample", "bulk", "sewing_charge"} else "bulk"


def _invoice_layout_title(inv: Invoice) -> str:
    return INVOICE_LAYOUT_TITLES.get((_invoice_market(inv), _invoice_type(inv)), "Invoice")


def _sync_invoice_market_region(inv: Invoice) -> None:
    market = _invoice_market(inv)
    inv.invoice_market = market
    inv.invoice_region = INVOICE_MARKET_TO_REGION.get(market, "CA")
    invoice_settings = _invoice_settings()
    default_currency_bd = getattr(invoice_settings, "default_currency_bd", "") or "BDT"
    default_currency_na = getattr(invoice_settings, "default_currency_na", "") or "CAD"
    if market == "bangladesh" and not inv.currency:
        inv.currency = default_currency_bd
    elif market == "north_america" and not inv.currency:
        inv.currency = default_currency_na
    if _invoice_type(inv) == "sample" and _d(getattr(inv, "deposit_percentage", None)) <= 0:
        inv.deposit_percentage = getattr(invoice_settings, "default_sample_deposit_percentage", None) or Decimal("100")
    elif market == "bangladesh" and _invoice_type(inv) == "sewing_charge" and _d(getattr(inv, "deposit_percentage", None)) <= 0:
        inv.deposit_percentage = getattr(invoice_settings, "default_bd_sewing_deposit_percentage", None) or Decimal("50")
    elif _d(getattr(inv, "deposit_percentage", None)) <= 0:
        inv.deposit_percentage = getattr(invoice_settings, "default_bulk_deposit_percentage", None) or Decimal("50")


def _invoice_company(region: str) -> dict:
    region = "BD" if region == "BD" else "CA"
    invoice_settings = _invoice_settings()
    return {
        "name": getattr(invoice_settings, "company_name", "") or getattr(settings, "INVOICE_COMPANY_NAME", "Iconic Apparel House Inc."),
        "email": getattr(invoice_settings, "company_email", "") or getattr(settings, "INVOICE_COMPANY_EMAIL", "info@iconicapparelhouse.com"),
        "phone": getattr(invoice_settings, "company_phone", "") or getattr(settings, "INVOICE_COMPANY_PHONE", "604-500-6009"),
        "website": getattr(invoice_settings, "website", "") or getattr(settings, "INVOICE_COMPANY_WEBSITE", "iconicapparelhouse.com"),
        "logo_path": getattr(settings, "INVOICE_LOGO_PATH", "img/image.png"),
        "slogan": getattr(invoice_settings, "slogan", "") or "From Concept to Creation",
        "footer_note": getattr(invoice_settings, "invoice_footer_note", "") or "Iconic Apparel House Inc. Your Trusted Manufacturing Partner for Growth.",
        "authorized_by_name": getattr(invoice_settings, "authorized_by_name", "") or "",
        "authorized_by_title": getattr(invoice_settings, "authorized_by_title", "") or "",
        "office_label": "Bangladesh" if region == "BD" else "Canada",
        "address": getattr(settings, f"INVOICE_ADDRESS_{region}", ""),
        "tax_label": getattr(settings, f"INVOICE_TAX_LABEL_{region}", ""),
        "tax_id": getattr(settings, f"INVOICE_TAX_ID_{region}", ""),
    }


def _invoice_policy_text(inv: Invoice) -> str:
    override = (getattr(inv, "terms_override", "") or "").strip()
    if override:
        return override
    invoice_settings = _invoice_settings()
    if _invoice_market(inv) == "bangladesh":
        return getattr(invoice_settings, "terms_and_conditions_bd", "") or BANGLADESH_INVOICE_TERMS
    return getattr(invoice_settings, "terms_and_conditions_na", "") or NORTH_AMERICA_INVOICE_TERMS


def _invoice_tax_note() -> str:
    invoice_settings = _invoice_settings()
    return getattr(invoice_settings, "default_tax_note", "") or ""


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


def _display_user(user) -> str:
    if not user:
        return ""
    full_name = ""
    try:
        full_name = user.get_full_name()
    except Exception:
        full_name = ""
    return full_name or getattr(user, "username", "") or str(user)


def _invoice_crm_references(inv: Invoice) -> dict:
    order = getattr(inv, "order", None)
    costing = getattr(inv, "costing_header", None)
    quick_costing = getattr(inv, "quick_costing", None)
    opportunity = None
    lead = None

    if order:
        opportunity = getattr(order, "opportunity", None)
        lead = getattr(order, "lead", None)
    if not opportunity and costing:
        opportunity = getattr(costing, "opportunity", None)
    if not opportunity and quick_costing:
        opportunity = getattr(quick_costing, "opportunity", None)
    if not lead and opportunity:
        lead = getattr(opportunity, "lead", None)

    account_manager = ""
    if lead:
        account_manager = _display_user(getattr(lead, "assigned_to", None)) or getattr(lead, "owner", "") or ""

    return {
        "lead": lead,
        "opportunity": opportunity,
        "production": order,
        "lead_id": getattr(lead, "lead_id", "") or getattr(lead, "pk", "") or "",
        "opportunity_id": getattr(opportunity, "opportunity_id", "") or getattr(opportunity, "pk", "") or "",
        "production_id": getattr(order, "purchase_order_number", "") or getattr(order, "pk", "") or "",
        "account_manager": account_manager or "N/A",
    }


def _invoice_deposit_terms(inv: Invoice) -> dict:
    percentage = _d(getattr(inv, "deposit_percentage", Decimal("0")))
    total = _d(getattr(inv, "total_amount", Decimal("0")))
    deposit_amount = _d(getattr(inv, "deposit_amount", Decimal("0")))
    balance_due = total - deposit_amount
    if balance_due < 0:
        balance_due = Decimal("0")
    return {
        "percentage": percentage,
        "deposit_amount": deposit_amount,
        "balance_due": balance_due.quantize(Decimal("0.01")),
        "deposit_label": "Advance Required" if _invoice_market(inv) == "bangladesh" else "Deposit Required",
        "balance_label": "Remaining Balance" if _invoice_market(inv) == "bangladesh" else "Balance Due Before Shipment",
    }


def _static_if_exists(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    return path if finders.find(path) else ""


def _invoice_default_deposit_values() -> dict:
    invoice_settings = _invoice_settings()
    return {
        "sample": getattr(invoice_settings, "default_sample_deposit_percentage", None) or Decimal("100.00"),
        "bulk": getattr(invoice_settings, "default_bulk_deposit_percentage", None) or Decimal("50.00"),
        "bd_sewing": getattr(invoice_settings, "default_bd_sewing_deposit_percentage", None) or Decimal("50.00"),
    }


def _default_deposit_for(market: str, invoice_type: str) -> Decimal:
    defaults = _invoice_default_deposit_values()
    if invoice_type == "sample":
        return defaults["sample"]
    if market == "bangladesh" and invoice_type == "sewing_charge":
        return defaults["bd_sewing"]
    return defaults["bulk"]


def _invoice_form_extra_context() -> dict:
    defaults = _invoice_default_deposit_values()
    return {
        "invoice_default_deposits": {
            "sample": f"{defaults['sample']:.2f}",
            "bulk": f"{defaults['bulk']:.2f}",
            "bd_sewing": f"{defaults['bd_sewing']:.2f}",
        }
    }


def _invoice_payment_info(inv: Invoice) -> dict:
    market = _invoice_market(inv)
    invoice_settings = _invoice_settings()
    if market == "bangladesh":
        return {
            "title": "Bangladesh Payment Information",
            "bank_name": getattr(invoice_settings, "bd_bank_name", "") or getattr(settings, "INVOICE_BD_BANK_NAME", ""),
            "account_name": getattr(invoice_settings, "bd_account_name", "") or getattr(settings, "INVOICE_BD_BANK_ACCOUNT_NAME", ""),
            "account_number": getattr(invoice_settings, "bd_account_number", "") or getattr(settings, "INVOICE_BD_BANK_ACCOUNT_NUMBER", ""),
            "branch": getattr(invoice_settings, "bd_branch", "") or getattr(settings, "INVOICE_BD_BANK_BRANCH", ""),
            "routing_number": getattr(invoice_settings, "bd_routing_number", "") or getattr(settings, "INVOICE_BD_BANK_ROUTING", ""),
            "swift": getattr(invoice_settings, "bd_swift", "") or getattr(settings, "INVOICE_BD_BANK_SWIFT", ""),
            "bkash_number": getattr(invoice_settings, "bkash_number", "") or getattr(settings, "INVOICE_BD_BKASH_NUMBER", ""),
            "nagad_number": getattr(invoice_settings, "nagad_number", "") or getattr(settings, "INVOICE_BD_NAGAD_NUMBER", ""),
            "rocket_number": getattr(invoice_settings, "rocket_number", "") or getattr(settings, "INVOICE_BD_ROCKET_NUMBER", ""),
            "bkash_qr_path": _static_if_exists(getattr(settings, "INVOICE_BD_BKASH_QR_PATH", "")),
            "nagad_qr_path": _static_if_exists(getattr(settings, "INVOICE_BD_NAGAD_QR_PATH", "")),
            "rocket_qr_path": _static_if_exists(getattr(settings, "INVOICE_BD_ROCKET_QR_PATH", "")),
            "bkash_qr_url": _settings_file_url(invoice_settings, "bkash_qr_image"),
            "nagad_qr_url": _settings_file_url(invoice_settings, "nagad_qr_image"),
            "rocket_qr_url": _settings_file_url(invoice_settings, "rocket_qr_image"),
            "bkash_qr_file": _settings_file_path(invoice_settings, "bkash_qr_image"),
            "nagad_qr_file": _settings_file_path(invoice_settings, "nagad_qr_image"),
            "rocket_qr_file": _settings_file_path(invoice_settings, "rocket_qr_image"),
            "payment_terms": getattr(invoice_settings, "bd_payment_terms", "") or "",
            "note": getattr(invoice_settings, "bd_payment_terms", "") or getattr(settings, "INVOICE_BD_PAYMENT_NOTE", ""),
        }

    return {
        "title": "North America Payment Information",
        "etransfer_email": getattr(invoice_settings, "etransfer_email", "") or getattr(settings, "INVOICE_CA_ETRANSFER_EMAIL", "") or "accounts@iconicapparelhouse.com",
        "etransfer_name": getattr(settings, "INVOICE_CA_ETRANSFER_NAME", ""),
        "paypal_id": getattr(invoice_settings, "paypal_email_or_id", "") or getattr(settings, "INVOICE_CA_PAYPAL_EMAIL", "") or getattr(settings, "INVOICE_PAYPAL_EMAIL", "") or "iconicapparelhouse",
        "paypal_qr_path": _static_if_exists(getattr(settings, "INVOICE_CA_PAYPAL_QR_PATH", "")),
        "paypal_qr_url": _settings_file_url(invoice_settings, "paypal_qr_image"),
        "paypal_qr_file": _settings_file_path(invoice_settings, "paypal_qr_image"),
        "bank_name": getattr(invoice_settings, "canada_bank_name", "") or getattr(settings, "INVOICE_CA_BANK_NAME", ""),
        "account_name": getattr(invoice_settings, "canada_account_name", "") or getattr(settings, "INVOICE_CA_BANK_ACCOUNT_NAME", ""),
        "account_number": getattr(invoice_settings, "canada_account_number", "") or getattr(settings, "INVOICE_CA_BANK_ACCOUNT_NUMBER", ""),
        "institution": getattr(invoice_settings, "canada_institution_number", "") or getattr(settings, "INVOICE_CA_BANK_INSTITUTION", ""),
        "transit": getattr(invoice_settings, "canada_transit_number", "") or getattr(settings, "INVOICE_CA_BANK_TRANSIT", ""),
        "swift": getattr(settings, "INVOICE_CA_BANK_SWIFT", ""),
        "wire_note": getattr(invoice_settings, "canada_wire_note", "") or "",
        "payment_terms": getattr(invoice_settings, "canada_payment_terms", "") or "",
        "note": getattr(invoice_settings, "canada_wire_note", "") or getattr(settings, "INVOICE_CA_PAYMENT_NOTE", ""),
    }


def _invoice_sewing_charge_line_items(inv: Invoice, qty: Decimal, sewing_total: Decimal) -> list[dict]:
    order = getattr(inv, "order", None)
    quick_costing = getattr(inv, "quick_costing", None)
    if qty <= 0 and quick_costing:
        qty = _d(getattr(quick_costing, "quantity", Decimal("0")))

    def _line_label(line):
        label = (getattr(line, "style_name", "") or "").strip()
        color = (getattr(line, "color_info", "") or "").strip()
        if label and color:
            return f"{label} - {color}"
        return label or color or "Sewing Charge"

    def _row(description, row_qty, row_rate, row_amount, *, quantity_unavailable=False, style_count=1):
        row_qty = _d(row_qty)
        row_rate = _d(row_rate)
        row_amount = _d(row_amount)
        return {
            "description": description or "Sewing Charge",
            "qty": row_qty,
            "rate": row_rate,
            "amount": row_amount,
            "has_qty": row_qty > 0 and not quantity_unavailable,
            "has_rate": row_rate > 0 and not quantity_unavailable,
            "has_amount": row_amount > 0,
            "is_detail": False,
            "is_sewing_charge": True,
            "quantity_unavailable": quantity_unavailable,
            "style_count": max(int(style_count or 1), 1),
        }

    if order and hasattr(order, "lines"):
        try:
            production_lines = list(order.lines.all().order_by("line_no", "id"))
        except Exception:
            production_lines = []
        if production_lines:
            line_quantities = []
            for line in production_lines:
                line_qty = _d(getattr(line, "quantity", None))
                line_quantities.append((_line_label(line), line_qty if line_qty > 0 else None))

            if all(line_qty is not None for _, line_qty in line_quantities):
                total_line_qty = sum((line_qty for _, line_qty in line_quantities), Decimal("0"))
                if total_line_qty > 0:
                    rate = (sewing_total / total_line_qty).quantize(Decimal("0.01")) if sewing_total > 0 else Decimal("0")
                    rows = []
                    allocated_total = Decimal("0")
                    for index, (style_name, line_qty) in enumerate(line_quantities, start=1):
                        is_last = index == len(line_quantities)
                        row_amount = (rate * line_qty).quantize(Decimal("0.01")) if sewing_total > 0 else Decimal("0")
                        if is_last and sewing_total > 0:
                            row_amount = sewing_total - allocated_total
                        rows.append(_row(style_name, line_qty, rate, row_amount))
                        allocated_total += row_amount
                    return rows

            description = "Consolidated Sewing Charge"
            if len(production_lines) > 1:
                description += " (style quantities unavailable)"
            consolidated_rate = (sewing_total / qty).quantize(Decimal("0.01")) if qty > 0 and sewing_total > 0 else Decimal("0")
            return [
                _row(
                    description,
                    qty,
                    consolidated_rate,
                    sewing_total,
                    quantity_unavailable=qty <= 0,
                    style_count=len(production_lines),
                )
            ]

    if order:
        description = (
            getattr(order, "style_name", "")
            or getattr(order, "title", "")
            or getattr(order, "purchase_order_number", "")
            or "Sewing Charge"
        ).strip()
        rate = (sewing_total / qty).quantize(Decimal("0.01")) if qty > 0 and sewing_total > 0 else Decimal("0")
        return [_row(description, qty, rate, sewing_total, quantity_unavailable=qty <= 0)]

    if quick_costing:
        description = (
            getattr(quick_costing, "project_name", "")
            or getattr(quick_costing, "product_type", "")
            or "Sewing Charge"
        ).strip()
        rate = (sewing_total / qty).quantize(Decimal("0.01")) if qty > 0 and sewing_total > 0 else Decimal("0")
        return [_row(description, qty, rate, sewing_total, quantity_unavailable=qty <= 0)]

    return [_row("Sewing Charge", qty, Decimal("0"), sewing_total, quantity_unavailable=True)]


def _invoice_line_items(inv: Invoice) -> list[dict]:
    order = getattr(inv, "order", None)
    quick_costing = getattr(inv, "quick_costing", None)
    subtotal = _d(getattr(inv, "subtotal", Decimal("0")))
    if order:
        qty = _d(getattr(order, "qty_total", Decimal("0")))
    elif quick_costing:
        qty = _d(getattr(quick_costing, "quantity", Decimal("0")))
    else:
        qty = Decimal("0")
    rate = Decimal("0")

    if _invoice_market(inv) == "bangladesh" and _invoice_type(inv) == "sewing_charge":
        sewing_total = subtotal if subtotal > 0 else _d(getattr(inv, "sewing_charge", Decimal("0")))
        return _invoice_sewing_charge_line_items(inv, qty, sewing_total)

    if qty > 0 and subtotal > 0:
        rate = (subtotal / qty).quantize(Decimal("0.01"))

    invoice_type = _invoice_type(inv)
    if invoice_type == "sample":
        description = "Sample Development"
    elif _invoice_market(inv) == "bangladesh":
        description = "Bangladesh Bulk Production"
    else:
        description = "Bulk Production"
    detail_parts = []
    if quick_costing and not order:
        description = (
            getattr(quick_costing, "project_name", "")
            or getattr(quick_costing, "get_product_type_display", lambda: "")()
            or description
        )
        if getattr(quick_costing, "buyer_name", ""):
            detail_parts.append(f"Buyer: {quick_costing.buyer_name}")
        if getattr(quick_costing, "quotation_number", ""):
            detail_parts.append(f"Quotation: {quick_costing.quotation_number}")
        if getattr(quick_costing, "costing_purpose", ""):
            detail_parts.append(f"Purpose: {quick_costing.purpose_label}")
    if order:
        description = getattr(order, "title", "") or getattr(order, "style_name", "") or getattr(order, "purchase_order_number", "") or description
        if getattr(order, "style_name", ""):
            detail_parts.append(f"Style: {order.style_name}")
        if getattr(order, "color_info", ""):
            detail_parts.append(f"Color: {order.color_info}")
        if getattr(order, "purchase_order_number", ""):
            detail_parts.append(f"Purchase Order Number: {order.purchase_order_number}")

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
            "is_sewing_charge": False,
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


def _invoice_sewing_summary(line_items: list[dict]) -> dict:
    style_rows = [item for item in line_items if item.get("is_sewing_charge") and not item.get("is_detail")]
    total_quantity = sum((_d(item.get("qty")) for item in style_rows), Decimal("0"))
    grand_total = sum((_d(item.get("amount")) for item in style_rows), Decimal("0"))
    quantity_unavailable = any(item.get("quantity_unavailable") for item in style_rows)
    style_count = sum((int(item.get("style_count") or 1) for item in style_rows), 0)
    return {
        "style_count": style_count or len(style_rows),
        "total_quantity": total_quantity,
        "grand_total": grand_total,
        "quantity_unavailable": quantity_unavailable,
    }


def _invoice_client_context(inv: Invoice, user=None) -> dict:
    market = _invoice_market(inv)
    invoice_type = _invoice_type(inv)
    region = _invoice_region(inv)
    line_items = _invoice_line_items(inv)
    is_bd_sewing_charge_invoice = market == "bangladesh" and invoice_type == "sewing_charge"
    inv = _sanitize_invoice_internal_fields(inv)
    return {
        "invoice": inv,
        "company": _invoice_company(region),
        "line_items": line_items,
        "sewing_summary": _invoice_sewing_summary(line_items) if is_bd_sewing_charge_invoice else None,
        "payment_status": _invoice_payment_status(inv),
        "policy_text": _invoice_policy_text(inv),
        "tax_note": _invoice_tax_note(),
        "can_approve_invoice": can_manage_invoices(user),
        "invoice_market": market,
        "invoice_market_label": dict(Invoice.INVOICE_MARKET_CHOICES).get(market, "North America"),
        "invoice_type": invoice_type,
        "invoice_type_label": dict(Invoice.INVOICE_TYPE_CHOICES).get(invoice_type, "Bulk Production"),
        "invoice_layout_title": _invoice_layout_title(inv),
        "crm_refs": _invoice_crm_references(inv),
        "deposit_terms": _invoice_deposit_terms(inv),
        "payment_info": _invoice_payment_info(inv),
        "is_sample_invoice": invoice_type == "sample",
        "is_bulk_invoice": invoice_type == "bulk",
        "is_bd_sewing_charge_invoice": is_bd_sewing_charge_invoice,
    }


def _invoice_client_template(inv: Invoice) -> str:
    return "crm/invoice/invoice_bd.html" if _invoice_market(inv) == "bangladesh" else "crm/invoice/invoice_ca.html"


@login_required
@user_passes_test(can_manage_invoice_settings)
def invoice_settings(request):
    settings_obj = _invoice_settings() or InvoiceSettings(is_active=True)
    if request.method == "POST":
        form = InvoiceSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.updated_by = request.user
            if not obj.pk:
                obj.is_active = True
            obj.save()
            messages.success(request, "Invoice settings saved.")
            return redirect("invoice_settings")
    else:
        form = InvoiceSettingsForm(instance=settings_obj)

    return render(
        request,
        "crm/invoice/invoice_settings.html",
        {
            "form": form,
            "settings_obj": settings_obj if settings_obj.pk else None,
        },
    )


def _preview_invoice(preview_type: str) -> Invoice:
    today = timezone.localdate()
    invoice_settings = _invoice_settings()
    currency_na = getattr(invoice_settings, "default_currency_na", "") or "CAD"
    currency_bd = getattr(invoice_settings, "default_currency_bd", "") or "BDT"
    mapping = {
        "north-america": ("north_america", "bulk", currency_na, Decimal("12500.00"), Decimal("450.00"), "North America Bulk Preview"),
        "bangladesh": ("bangladesh", "bulk", currency_bd, Decimal("960000.00"), Decimal("0.00"), "Bangladesh Bulk Preview"),
        "sewing-charge": ("bangladesh", "sewing_charge", currency_bd, Decimal("180000.00"), Decimal("0.00"), "Bangladesh Sewing Charge Preview"),
    }
    market, invoice_type, currency, subtotal, shipping, title = mapping.get(preview_type, mapping["north-america"])
    inv = Invoice(
        invoice_number=f"PREVIEW-{preview_type.upper()}",
        issue_date=today,
        due_date=today + timedelta(days=7),
        invoice_market=market,
        invoice_region=INVOICE_MARKET_TO_REGION.get(market, "CA"),
        invoice_type=invoice_type,
        currency=currency,
        subtotal=subtotal,
        shipping_amount=shipping,
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        total_amount=subtotal + shipping,
        paid_amount=Decimal("0.00"),
        deposit_percentage=_default_deposit_for(market, invoice_type),
        status="sent",
    )
    inv.customer = Customer(
        account_brand="Preview Client",
        contact_name="Preview Buyer",
        email="buyer@example.com",
        country="Canada" if market == "north_america" else "Bangladesh",
    )
    if invoice_type == "sewing_charge":
        inv.sewing_charge = subtotal
    inv.preview_title = title
    return inv


@login_required
@user_passes_test(can_manage_invoice_settings)
def invoice_settings_preview(request, preview_type):
    inv = _preview_invoice(preview_type)
    context = _invoice_client_context(inv, request.user)
    context["is_preview"] = True
    context["preview_title"] = getattr(inv, "preview_title", "Invoice Preview")
    return render(request, _invoice_client_template(inv), context)


@login_required
@user_passes_test(superuser_only)
def accounts_receivable_dashboard(request):
    can_include_archived = can_archive_invoice(request.user)
    filters = {
        "date_from": _parse_ar_date(request.GET.get("date_from")),
        "date_to": _parse_ar_date(request.GET.get("date_to")),
        "customer_id": (request.GET.get("customer") or "").strip(),
        "status": (request.GET.get("status") or "").strip(),
        "currency": (request.GET.get("currency") or "").strip().upper(),
        "side": (request.GET.get("side") or "").strip().upper(),
        "production_linked": (request.GET.get("production_linked") or "") == "1",
        "include_archived": can_include_archived and (request.GET.get("include_archived") or "") == "1",
    }
    filter_values = {
        "date_from": filters["date_from"].isoformat() if filters["date_from"] else "",
        "date_to": filters["date_to"].isoformat() if filters["date_to"] else "",
        "customer": filters["customer_id"],
        "status": filters["status"],
        "currency": filters["currency"],
        "side": filters["side"],
        "production_linked": filters["production_linked"],
        "include_archived": filters["include_archived"],
        "can_include_archived": can_include_archived,
    }

    invoices_qs = _invoice_archive_scope(
        Invoice.objects.select_related("order", "customer"),
        "all" if filters["include_archived"] else "active",
    )
    invoices_qs = _apply_ar_invoice_filters(invoices_qs, filters)
    invoice_rows = list(invoices_qs.order_by("due_date", "-issue_date", "-created_at"))

    payments_qs = InvoicePayment.objects.select_related(
        "invoice",
        "invoice__customer",
        "production_order",
        "accounting_entry",
    )
    payments_qs = _invoice_payment_archive_scope(payments_qs, filters["include_archived"])
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
                "currency_map": {},
            }
        monthly_map[key]["received"] += _d(payment.amount)
        monthly_map[key]["received_bdt"] += _d(payment.amount_bdt)
        monthly_map[key]["payments"] += 1
        currency = (payment.currency or "Unknown").upper()
        monthly_map[key]["currency_map"][currency] = (
            monthly_map[key]["currency_map"].get(currency, Decimal("0")) + _d(payment.amount)
        )
    monthly_rows = [monthly_map[key] for key in sorted(monthly_map.keys())]
    max_monthly = max([row["payments"] for row in monthly_rows] or [0])
    for row in monthly_rows:
        row["currency_totals"] = [
            {"currency": currency, "amount": amount}
            for currency, amount in sorted(row["currency_map"].items())
            if amount != 0
        ]
        row["bar_percent"] = int((row["payments"] / max_monthly) * 100) if max_monthly > 0 else 0

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
            "aging_rows": _ar_aging_rows(open_invoices, today),
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
    customer_id = (request.GET.get("customer") or "").strip()
    paid_filter = (request.GET.get("paid") or "").strip()
    archive_filter = (request.GET.get("archive") or "active").strip().lower()
    can_archive = can_archive_invoice(request.user)
    if archive_filter not in {"active", "archived", "all"}:
        archive_filter = "active"
    if archive_filter != "active" and not can_archive:
        return HttpResponse("Archived invoices are restricted to CEO, Admin, and Accounts Manager users.", status=403)
    date_from = parse_date((request.GET.get("date_from") or "").strip())
    date_to = parse_date((request.GET.get("date_to") or "").strip())

    invoices = _invoice_archive_scope(Invoice.objects.select_related("order", "customer"), archive_filter)

    if q:
        invoices = invoices.filter(
            Q(invoice_number__icontains=q)
            | ProductionOrder.identifier_search_query(q, "order__order_code")
            | Q(order__title__icontains=q)
            | Q(customer__account_brand__icontains=q)
            | Q(customer__contact_name__icontains=q)
            | Q(customer__email__icontains=q)
        )

    if status:
        invoices = invoices.filter(status=status)

    if currency:
        invoices = invoices.filter(currency=currency)

    if customer_id:
        invoices = invoices.filter(customer_id=customer_id)

    if date_from:
        invoices = invoices.filter(issue_date__gte=date_from)

    if date_to:
        invoices = invoices.filter(issue_date__lte=date_to)

    if paid_filter == "paid":
        invoices = invoices.filter(status="paid")
    elif paid_filter == "unpaid":
        invoices = invoices.exclude(status="paid")

    invoices = invoices.order_by("-issue_date", "-created_at")
    invoice_rows = list(invoices)
    total_amount = sum((_d(inv.total_amount) for inv in invoice_rows), Decimal("0"))
    received_amount = sum((_d(inv.paid_amount) for inv in invoice_rows), Decimal("0"))
    unpaid_balance = sum((_d(inv.balance) for inv in invoice_rows), Decimal("0"))
    open_count = sum(1 for inv in invoice_rows if inv.payment_status_key in {"unpaid", "partial", "overpaid"})
    totals_by_currency = defaultdict(lambda: {"total": Decimal("0"), "received": Decimal("0"), "balance": Decimal("0")})
    for inv in invoice_rows:
        code = inv.currency or "USD"
        totals_by_currency[code]["total"] += _d(inv.total_amount)
        totals_by_currency[code]["received"] += _d(inv.paid_amount)
        totals_by_currency[code]["balance"] += _d(inv.balance)
    total_by_currency = [
        {"currency": code, "amount": values["total"]}
        for code, values in sorted(totals_by_currency.items())
    ]
    received_by_currency = [
        {"currency": code, "amount": values["received"]}
        for code, values in sorted(totals_by_currency.items())
    ]
    balance_by_currency = [
        {"currency": code, "amount": values["balance"]}
        for code, values in sorted(totals_by_currency.items())
    ]

    today = timezone.localdate()
    payment_summary_qs = InvoicePayment.objects.all()
    if archive_filter != "all":
        payment_summary_qs = payment_summary_qs.filter(invoice__is_archived=archive_filter == "archived")
    invoice_summary_qs = _invoice_archive_scope(Invoice.objects.all(), archive_filter)
    monthly_received = (
        payment_summary_qs.filter(payment_date__year=today.year, payment_date__month=today.month)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    bd_received_total = (
        invoice_summary_qs.filter(Q(invoice_region="BD") | Q(currency="BDT")).aggregate(total=Sum("paid_amount"))["total"]
        or Decimal("0")
    )
    bd_unpaid_balance = sum(
        (_d(inv.balance) for inv in invoice_summary_qs.filter(Q(invoice_region="BD") | Q(currency="BDT"))),
        Decimal("0"),
    )
    production_received_rows = list(
        payment_summary_qs.filter(production_order__isnull=False)
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
            "customer": customer_id,
            "paid": paid_filter,
            "archive_filter": archive_filter,
            "can_archive_invoices": can_archive,
            "date_from": date_from,
            "date_to": date_to,
            "customers": Customer.objects.order_by("account_brand", "contact_name", "id"),
            "currency_options": [choice[0] for choice in Invoice._meta.get_field("currency").choices],
            "total_amount": total_amount,
            "received_amount": received_amount,
            "unpaid_balance": unpaid_balance,
            "total_by_currency": total_by_currency,
            "received_by_currency": received_by_currency,
            "balance_by_currency": balance_by_currency,
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

    invoices = Invoice.objects.select_related("order", "customer").filter(currency=currency_code, is_archived=False)

    if q:
        invoices = invoices.filter(
            Q(invoice_number__icontains=q)
            | ProductionOrder.identifier_search_query(q, "order__order_code")
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
    initial = {"deposit_percentage": _default_deposit_for("north_america", "bulk")}
    local_order = _local_sewing_order(order_id)
    if local_order:
        initial.update(_local_sewing_invoice_initial(local_order))
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

                local_order = _local_sewing_order(inv.order_id)
                if local_order:
                    _apply_local_sewing_invoice_source(inv, local_order)
                _sync_invoice_market_region(inv)
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
        {"form": form, "mode": "add", "can_manage_invoice_costing": can_edit_internal_costs, **_invoice_form_extra_context()},
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
                inv.invoice_market = "north_america"
                inv.invoice_region = "CA"
                _sync_invoice_market_region(inv)
                _calc_totals(inv)
                inv.save()
                form.save_m2m()
            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        form = InvoiceForm(
            initial={
                "currency": "CAD",
                "invoice_market": "north_america",
                "invoice_region": "CA",
                "deposit_percentage": _default_deposit_for("north_america", "bulk"),
            },
            can_edit_internal_costs=can_edit_internal_costs,
        )
    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "add", "can_manage_invoice_costing": can_edit_internal_costs, **_invoice_form_extra_context()},
    )


@login_required
@user_passes_test(superuser_only)
def invoice_add_bd(request):
    # wrapper to force BDT
    can_edit_internal_costs = can_manage_invoice_internal_costing(request.user)
    order_id = request.GET.get("order_id") or request.POST.get("order")
    local_order = _local_sewing_order(order_id)
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
                local_order = _local_sewing_order(inv.order_id)
                if local_order:
                    _apply_local_sewing_invoice_source(inv, local_order)
                inv.invoice_market = "bangladesh"
                inv.invoice_region = "BD"
                _sync_invoice_market_region(inv)
                _calc_totals(inv)
                inv.save()
                form.save_m2m()
            messages.success(request, "Invoice created.")
            return redirect("invoice_view", pk=inv.pk)
    else:
        initial = {
                "currency": "BDT",
                "invoice_market": "bangladesh",
                "invoice_region": "BD",
                "deposit_percentage": _default_deposit_for("bangladesh", "bulk"),
            }
        if local_order:
            initial.update(_local_sewing_invoice_initial(local_order))
        form = InvoiceForm(
            initial=initial,
            can_edit_internal_costs=can_edit_internal_costs,
        )
    return render(
        request,
        "crm/invoice/invoice_form.html",
        {"form": form, "mode": "add", "can_manage_invoice_costing": can_edit_internal_costs, **_invoice_form_extra_context()},
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

                local_order = _local_sewing_order(inv2.order_id)
                if local_order:
                    inv2.currency = "BDT"
                    inv2.invoice_market = "bangladesh"
                    inv2.invoice_region = "BD"
                    inv2.invoice_type = "sewing_charge"

                _sync_invoice_market_region(inv2)
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
        {
            "form": form,
            "mode": "edit",
            "invoice": display_invoice,
            "can_manage_invoice_costing": can_edit_internal_costs,
            **_invoice_form_extra_context(),
        },
    )


@login_required
@user_passes_test(superuser_only)
def invoice_view(request, pk):
    inv = get_object_or_404(
        Invoice.objects.select_related(
            "order",
            "customer",
            "costing_header",
            "quick_costing",
            "quick_costing__opportunity",
            "quick_costing__opportunity__lead",
        ),
        pk=pk,
    )
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
        quick_costing=getattr(inv, "quick_costing", None),
        production_order=getattr(inv, "order", None),
        lifecycle=lifecycle,
    )
    invoice_market = _invoice_market(inv)
    invoice_type = _invoice_type(inv)

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
            "invoice_market_label": dict(Invoice.INVOICE_MARKET_CHOICES).get(invoice_market, "North America"),
            "invoice_type_label": dict(Invoice.INVOICE_TYPE_CHOICES).get(invoice_type, "Bulk Production"),
            "invoice_layout_title": _invoice_layout_title(inv),
            "crm_refs": _invoice_crm_references(inv),
            "deposit_terms": _invoice_deposit_terms(inv),
            "can_archive_invoice": can_archive_invoice(request.user),
            "invoice_has_payments": bool(payment_history or _d(inv.paid_amount) > 0),
            **workflow_visibility,
        },
    )


@login_required
@require_POST
def invoice_archive(request, pk):
    if not can_archive_invoice(request.user):
        return HttpResponse("Only CEO, Admin, and Accounts Manager users can archive invoices.", status=403)
    with transaction.atomic():
        invoice = get_object_or_404(Invoice.objects.select_for_update(), pk=pk)
        if invoice.is_archived:
            messages.info(request, f"Invoice {invoice.invoice_number} is already archived.")
            return redirect("invoice_view", pk=invoice.pk)
        if _d(invoice.paid_amount) > 0 or invoice.payments.exists():
            messages.error(
                request,
                "This invoice has payments recorded. Archive is not allowed unless payment is reversed first.",
            )
            return redirect("invoice_view", pk=invoice.pk)
        invoice.is_archived = True
        invoice.archived_at = timezone.now()
        invoice.archived_by = request.user
        invoice.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
    messages.success(request, f"Invoice {invoice.invoice_number} has been archived. Payment history was preserved.")
    return redirect(f"{reverse('invoice_list')}?archive=archived")


@login_required
@user_passes_test(superuser_only)
def invoice_client_view(request, pk):
    inv = get_object_or_404(
        Invoice.objects.select_related(
            "order",
            "customer",
            "quick_costing",
            "quick_costing__opportunity",
            "quick_costing__opportunity__lead",
        ),
        pk=pk,
    )
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
    inv = get_object_or_404(
        Invoice.objects.select_related(
            "order",
            "customer",
            "quick_costing",
            "quick_costing__opportunity",
            "quick_costing__opportunity__lead",
        ),
        pk=pk,
    )

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
    crm_refs = context["crm_refs"]
    deposit_terms = context["deposit_terms"]
    payment_info = context["payment_info"]
    tax_note = context["tax_note"]
    layout_title = context["invoice_layout_title"]
    is_bd_sewing_charge_invoice = context["is_bd_sewing_charge_invoice"]
    is_sample_invoice = context["is_sample_invoice"]
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
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawRightString(right, height - 90, layout_title[:48])

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
        f"Market: {context['invoice_market_label']}",
        f"Invoice type: {context['invoice_type_label']}",
        f"Payment status: {payment_status['label']}",
    ]
    if is_bd_sewing_charge_invoice and is_bangladesh_local_sewing(inv.order):
        detail_lines.extend(
            [
                "Service Type: Bangladesh Local Sewing",
                "Charge Type: CMT / Sewing Charge",
            ]
        )
    row_y = y
    for line in [line for line in company_lines if line]:
        pdf.drawString(left, row_y, line[:84])
        row_y -= 12
    row_y = y
    for line in detail_lines:
        pdf.drawString(width / 2 + 12, row_y, line)
        row_y -= 12
    y -= 114 if is_bd_sewing_charge_invoice and is_bangladesh_local_sewing(inv.order) else 90

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

    y -= 8
    ensure_space(70)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "CRM References")
    y -= 14
    pdf.setFont("Helvetica", 9)
    for line in [
        f"Lead ID: {crm_refs.get('lead_id') or 'N/A'}",
        f"Opportunity ID: {crm_refs.get('opportunity_id') or 'N/A'}",
        f"Purchase Order Number: {crm_refs.get('production_id') or 'N/A'}",
        f"Account Manager: {crm_refs.get('account_manager') or 'N/A'}",
    ]:
        pdf.drawString(left, y, line[:100])
        y -= 12

    y -= 14
    ensure_space(180)
    pdf.setFillColor(colors.HexColor("#f8fafc"))
    pdf.rect(left, y - 22, right - left, 24, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left + 8, y - 14, "Style" if is_bd_sewing_charge_invoice else "Description")
    pdf.drawRightString(right - 198, y - 14, "Quantity" if is_bd_sewing_charge_invoice else "Qty")
    pdf.drawRightString(right - 112, y - 14, "Sewing / Pc" if is_bd_sewing_charge_invoice else "Rate")
    pdf.drawRightString(right - 8, y - 14, "Total Sewing" if is_bd_sewing_charge_invoice else "Amount")
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
            if item.get("quantity_unavailable"):
                qty_text = "Unavailable"
            else:
                qty_text = f"{item['qty']:,.0f}" if item.get("has_qty") else "-"
            pdf.drawRightString(right - 198, start_y, qty_text)
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
        (f"{deposit_terms['deposit_label']} ({deposit_terms['percentage']:,.2f}%)", deposit_terms["deposit_amount"]),
        (deposit_terms["balance_label"], deposit_terms["balance_due"]),
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
    if tax_note:
        ensure_space(50)
        pdf.setFont("Helvetica", 8)
        for line in _pdf_text_lines(pdf, tax_note, int(right - summary_x), "Helvetica", 8):
            pdf.drawString(summary_x, y, line)
            y -= 10

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

    ensure_space(160)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Payment Terms")
    y -= 14
    pdf.setFont("Helvetica", 9)
    if payment_info.get("payment_terms"):
        term_line = payment_info["payment_terms"]
    elif is_sample_invoice:
        term_line = "100% payment is required before sample development begins."
    else:
        term_line = (
            f"{deposit_terms['percentage']:,.2f}% deposit/advance is required. "
            f"{deposit_terms['balance_label']} is due before shipment or delivery."
        )
    for line in _pdf_text_lines(pdf, term_line, int(right - left), "Helvetica", 9):
        pdf.drawString(left, y, line)
        y -= 12
    y -= 8

    ensure_space(170)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, payment_info.get("title", "Payment Information"))
    y -= 14
    pdf.setFont("Helvetica", 8)
    if context["invoice_market"] == "bangladesh":
        payment_lines = [
            ("Bank", payment_info.get("bank_name")),
            ("Account name", payment_info.get("account_name")),
            ("Account number", payment_info.get("account_number")),
            ("Branch", payment_info.get("branch")),
            ("Routing number", payment_info.get("routing_number")),
            ("SWIFT", payment_info.get("swift")),
            ("bKash", payment_info.get("bkash_number")),
            ("Nagad", payment_info.get("nagad_number")),
            ("Rocket", payment_info.get("rocket_number")),
        ]
    else:
        payment_lines = [
            ("E Transfer", payment_info.get("etransfer_email")),
            ("PayPal ID", payment_info.get("paypal_id")),
            ("Bank", payment_info.get("bank_name")),
            ("Account name", payment_info.get("account_name")),
            ("Account number", payment_info.get("account_number")),
            ("Institution", payment_info.get("institution")),
            ("Transit", payment_info.get("transit")),
            ("SWIFT", payment_info.get("swift")),
        ]
    rendered_payment = False
    for label, value in payment_lines:
        if value:
            ensure_space(50)
            pdf.drawString(left, y, f"{label}: {value}"[:120])
            y -= 11
            rendered_payment = True
    if payment_info.get("note"):
        for line in _pdf_text_lines(pdf, payment_info["note"], int(right - left), "Helvetica", 8):
            ensure_space(50)
            pdf.drawString(left, y, line)
            y -= 11
            rendered_payment = True
    if payment_info.get("wire_note") and payment_info.get("wire_note") != payment_info.get("note"):
        for line in _pdf_text_lines(pdf, payment_info["wire_note"], int(right - left), "Helvetica", 8):
            ensure_space(50)
            pdf.drawString(left, y, line)
            y -= 11
            rendered_payment = True
    if not rendered_payment:
        pdf.drawString(left, y, "Payment details will be provided by our accounts team.")
        y -= 11
    qr_paths = [
        payment_info.get("paypal_qr_path"),
        payment_info.get("bkash_qr_path"),
        payment_info.get("nagad_qr_path"),
        payment_info.get("rocket_qr_path"),
    ]
    qr_files = [
        payment_info.get("paypal_qr_file"),
        payment_info.get("bkash_qr_file"),
        payment_info.get("nagad_qr_file"),
        payment_info.get("rocket_qr_file"),
    ]
    qr_files.extend(finders.find(path) for path in qr_paths if path)
    qr_files = [path for path in qr_files if path]
    if qr_files:
        ensure_space(130)
        qr_x = left
        qr_y = y - 92
        for qr_file in qr_files[:3]:
            try:
                pdf.drawImage(ImageReader(qr_file), qr_x, qr_y, width=82, height=82, preserveAspectRatio=True, mask="auto")
                qr_x += 96
            except Exception:
                continue
        y -= 104
    y -= 8

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

    y -= 14
    ensure_space(90)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Authorized By")
    pdf.drawString(width / 2 + 12, y, "Client Agreement")
    y -= 34
    pdf.setStrokeColor(colors.HexColor("#94a3b8"))
    pdf.line(left, y, left + 190, y)
    pdf.line(width / 2 + 12, y, right, y)
    y -= 12
    authorized_by = company.get("authorized_by_name") or ""
    authorized_title = company.get("authorized_by_title") or ""
    if authorized_by:
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(left, y, authorized_by[:42])
        y -= 10
        if authorized_title:
            pdf.setFont("Helvetica", 8)
            pdf.drawString(left, y, authorized_title[:42])
            y -= 10
    else:
        y -= 10
    y -= 22
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(width / 2, y, "Thank You! For Your Business")
    y -= 14
    pdf.setFont("Helvetica", 8)
    if company.get("slogan"):
        pdf.drawCentredString(width / 2, y, company["slogan"][:90])
        y -= 11
    if company.get("footer_note"):
        pdf.drawCentredString(width / 2, y, company["footer_note"][:100])

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
        messages.success(request, f"Production order {order.purchase_order_number or order.pk} created from invoice.")
    else:
        messages.info(request, f"Invoice linked to production order {order.purchase_order_number or order.pk}.")
    return redirect("production_detail", pk=order.pk)
