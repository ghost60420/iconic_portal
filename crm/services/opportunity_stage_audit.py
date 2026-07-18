import csv
import io
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models import Q
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from crm.models import (
    AutomationNotification,
    CostingHeader,
    Invoice,
    OrderLifecycle,
    Opportunity,
    ProductionOrder,
    QuickCosting,
    Shipment,
)
from crm.services.costing_currency import currency_summary_rows, format_finance_money
from crm.services.opportunity_payment_stage import AWAITING_PAYMENT_STAGE, decimal_or_zero, invoice_open_balance


COMPLETED_STAGE_VALUES = {"Shipment Complete", "Closed Won"}
PRODUCTION_STAGE_VALUES = {"Production"}
NEGOTIATION_STAGE_VALUES = {"Negotiation"}
ARCHIVED_CATEGORY = "Archived"
PROPOSAL_CATEGORY = "Proposal"
NEGOTIATION_CATEGORY = "Negotiation"
AWAITING_PAYMENT_CATEGORY = "Awaiting Payment"
PRODUCTION_CATEGORY = "Production"
COMPLETED_CATEGORY = "Completed"
REPORT_CATEGORIES = (
    PROPOSAL_CATEGORY,
    NEGOTIATION_CATEGORY,
    AWAITING_PAYMENT_CATEGORY,
    PRODUCTION_CATEGORY,
    COMPLETED_CATEGORY,
    ARCHIVED_CATEGORY,
)
DETAIL_REPORT_SECTIONS = (
    ("broken_production_links", "Broken Production Links"),
    ("broken_invoice_links", "Broken Invoice Links"),
    ("stage_mismatches", "Stage Mismatches"),
    ("shipment_completion_mismatches", "Shipment Completion Mismatches"),
    ("awaiting_payment_errors", "Awaiting Payment Errors"),
    ("legacy_test_data", "Legacy Test Data"),
)
REPAIR_SAFE_AUTO_FIX = "SAFE_AUTO_FIX"
REPAIR_MANUAL_REVIEW = "MANUAL_REVIEW"
REPAIR_IGNORE_LEGACY_TEST = "IGNORE_LEGACY_TEST"
AUDIT_NOTIFICATION_SOURCE_KEY = "opportunity-stage-audit:summary:ceo"
LEGACY_TEST_RE = re.compile(
    r"(^|[^a-z0-9])(test|demo|dummy|sandbox|example|qa record|legacy test)([^a-z0-9]|$)",
    re.IGNORECASE,
)
CSV_COLUMNS = (
    "section",
    "warning_code",
    "repair_classification",
    "opportunity_id",
    "opportunity_number",
    "customer_name",
    "current_stage",
    "expected_stage",
    "invoice_id",
    "invoice_status",
    "outstanding_balance",
    "production_order_id",
    "shipment_id",
    "lifecycle_id",
    "assigned_salesperson",
    "created_date",
    "historical_entry_status",
    "legacy_test_status",
    "reason_for_failure",
    "recommended_repair_action",
)


def _safe_text(value):
    return str(value or "").strip()


def _display_user(user):
    if not user:
        return ""
    full_name = _safe_text(getattr(user, "get_full_name", lambda: "")())
    return full_name or _safe_text(getattr(user, "username", "")) or _safe_text(getattr(user, "email", ""))


def _date_display(value):
    if not value:
        return ""
    if hasattr(value, "date"):
        value = value.date()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _join_ids(values):
    clean = []
    for value in values or []:
        if value in (None, ""):
            continue
        text = str(value)
        if text not in clean:
            clean.append(text)
    return ", ".join(clean)


def _customer_label(opportunity):
    customer = getattr(opportunity, "customer", None)
    lead_customer = getattr(getattr(opportunity, "lead", None), "customer", None)
    source = customer or lead_customer
    if not source:
        return ""
    label = (
        _safe_text(getattr(source, "account_brand", ""))
        or _safe_text(getattr(source, "contact_name", ""))
        or _safe_text(getattr(source, "customer_code", ""))
    )
    if source == lead_customer and not customer:
        return f"{label} (via lead)" if label else "Linked lead customer"
    return label


def _legacy_test_reason(opportunity):
    customer = getattr(opportunity, "customer", None)
    lead = getattr(opportunity, "lead", None)
    lead_customer = getattr(lead, "customer", None)
    values = [
        ("opportunity number", getattr(opportunity, "opportunity_id", "")),
        ("opportunity notes", getattr(opportunity, "notes", "")),
        ("product type", getattr(opportunity, "product_type", "")),
        ("product category", getattr(opportunity, "product_category", "")),
        ("customer brand", getattr(customer, "account_brand", "")),
        ("customer contact", getattr(customer, "contact_name", "")),
        ("customer email", getattr(customer, "email", "")),
        ("lead brand", getattr(lead, "account_brand", "")),
        ("lead contact", getattr(lead, "contact_name", "")),
        ("lead email", getattr(lead, "email", "")),
        ("lead customer brand", getattr(lead_customer, "account_brand", "")),
        ("lead customer contact", getattr(lead_customer, "contact_name", "")),
    ]
    for label, value in values:
        text = _safe_text(value)
        if text and LEGACY_TEST_RE.search(text):
            return f"{label} contains legacy/test marker"
    return ""


def _is_historical_entry(opportunity, invoices):
    if getattr(opportunity, "opportunity_date", None):
        return True
    return any(getattr(invoice, "is_historical_entry", False) for invoice in invoices)


def _historical_entry_status(opportunity, invoices):
    parts = []
    if getattr(opportunity, "opportunity_date", None):
        parts.append(f"Opportunity Date {_date_display(opportunity.opportunity_date)}")
    invoice_dates = [
        f"{invoice.invoice_number or invoice.pk}: {_date_display(invoice.invoice_date)}"
        for invoice in invoices
        if getattr(invoice, "is_historical_entry", False)
    ]
    if invoice_dates:
        parts.append("Historical invoice date " + "; ".join(invoice_dates))
    return "Yes - " + " | ".join(parts) if parts else "No"


def _warning_section(code):
    if code == "legacy_test_candidate":
        return "legacy_test_data"
    if code in {"production_stage_incorrect", "production_link_missing", "duplicate_production_links"}:
        return "broken_production_links"
    if code in {"invoice_link_missing", "invoice_link_conflict"}:
        return "broken_invoice_links"
    if code == "completed_stage_incorrect":
        return "shipment_completion_mismatches"
    if code in {"invoice_stage_incorrect", "awaiting_payment_invalid", "negotiation_has_invoice"}:
        return "awaiting_payment_errors"
    return "stage_mismatches"


def _health_color(value):
    value = int(value or 0)
    if value <= 0:
        return "green"
    if value <= 5:
        return "yellow"
    return "red"


def _parse_database_datetime(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value
    return parse_datetime(str(value))


def _format_balance_for_invoices(invoices):
    open_balance_totals = defaultdict(lambda: {"amount": Decimal("0")})
    for invoice in invoices:
        balance = invoice_open_balance(invoice)
        if balance <= 0:
            continue
        currency = (invoice.currency or "CAD").upper()
        open_balance_totals[currency]["amount"] += balance
    balance_rows = currency_summary_rows(open_balance_totals)
    return " / ".join(
        format_finance_money(row["amount"], row["currency"]) for row in balance_rows
    ) or "-"


def _costing_type_for_opportunity(costing_types):
    if not costing_types:
        return ""
    return " / ".join(sorted(costing_types))


def _recommended_action(code, row):
    expected = row.get("expected_category") or ""
    current = row.get("current_stage") or ""
    if code == "production_link_missing":
        if row.get("open_invoice_count"):
            return "Dry-run recommendation: keep no ProductionOrder and move opportunity to Awaiting Payment after approval."
        return "Dry-run recommendation: review paid invoice/costing source before creating or linking a ProductionOrder."
    if code == "production_stage_incorrect":
        return f"Dry-run recommendation: set opportunity stage to {expected} if linked production order is valid."
    if code == "duplicate_production_links":
        return "Dry-run recommendation: manual review required; choose the valid ProductionOrder link before any repair."
    if code == "invoice_stage_incorrect":
        return "Dry-run recommendation: set opportunity stage to Awaiting Payment because an open invoice balance exists and no production order is linked."
    if code == "awaiting_payment_invalid":
        return "Dry-run recommendation: review invoice balance and production links before moving out of Awaiting Payment."
    if code == "completed_stage_incorrect":
        return "Dry-run recommendation: set opportunity stage to Completed after confirming the completed shipment is final."
    if code == "proposal_has_downstream_records":
        return f"Dry-run recommendation: move opportunity from {current or 'current stage'} to {expected} after confirming downstream records."
    if code == "negotiation_has_invoice":
        return "Dry-run recommendation: move opportunity to Awaiting Payment if invoice has a balance, otherwise review lifecycle state."
    if code == "missing_customer":
        return "Dry-run recommendation: manually link the correct Customer; do not create a duplicate customer."
    if code in {"invoice_link_missing", "invoice_link_conflict"}:
        return "Dry-run recommendation: manually review invoice source links before attaching to an opportunity."
    if code == "legacy_test_candidate":
        return "Dry-run recommendation: ignore for operational repair unless CEO confirms this record is real production data."
    return f"Dry-run recommendation: review and align stage to {expected or 'the expected workflow state'}."


def _repair_classification(code, row):
    if row.get("legacy_test_reason") or code == "legacy_test_candidate":
        return REPAIR_IGNORE_LEGACY_TEST
    if code in {
        "invoice_stage_incorrect",
        "production_stage_incorrect",
        "completed_stage_incorrect",
        "proposal_has_downstream_records",
        "negotiation_has_invoice",
    }:
        return REPAIR_SAFE_AUTO_FIX
    return REPAIR_MANUAL_REVIEW


def _record_from_warning(warning, row=None, invoice=None):
    row = row or {}
    invoice_ids = row.get("invoice_ids") or []
    invoice_statuses = row.get("invoice_statuses") or []
    if invoice is not None:
        invoice_ids = [invoice.pk]
        invoice_statuses = [invoice.status]
    code = warning["code"]
    record = {
        "section": _warning_section(code),
        "warning_code": code,
        "opportunity_id": row.get("id") or "",
        "opportunity_number": row.get("opportunity_number") or warning.get("opportunity_number") or "",
        "customer_name": row.get("customer") or "",
        "current_stage": row.get("current_stage") or warning.get("stage") or "",
        "expected_stage": row.get("expected_category") or "",
        "invoice_id": _join_ids(invoice_ids),
        "invoice_status": _join_ids(invoice_statuses),
        "outstanding_balance": row.get("outstanding_balance") or "",
        "production_order_id": _join_ids(row.get("production_order_ids") or []),
        "shipment_id": _join_ids(row.get("shipment_ids") or []),
        "lifecycle_id": _join_ids(row.get("lifecycle_ids") or []),
        "assigned_salesperson": row.get("assigned_salesperson") or "",
        "created_date": row.get("created_date") or "",
        "historical_entry_status": row.get("historical_entry_status") or "No",
        "legacy_test_status": row.get("legacy_test_status") or "No",
        "legacy_test_reason": row.get("legacy_test_reason") or "",
        "reason_for_failure": warning["message"],
    }
    record["repair_classification"] = _repair_classification(code, row)
    record["recommended_repair_action"] = _recommended_action(code, row)
    return record


def _filter_detail_records(records, filter_mode):
    filter_mode = (filter_mode or "all").strip().lower()
    if filter_mode in {"", "all"}:
        return list(records)
    if filter_mode == "broken":
        return [record for record in records if record["warning_code"] != "legacy_test_candidate"]
    if filter_mode == "legacy":
        return [record for record in records if record["repair_classification"] == REPAIR_IGNORE_LEGACY_TEST]
    if filter_mode == "repairable":
        return [record for record in records if record["repair_classification"] == REPAIR_SAFE_AUTO_FIX]
    raise ValueError("Unknown integrity filter. Use all, broken, legacy, or repairable.")


def _resolved_opportunity_ids_for_invoice(invoice):
    ids = set()
    for attr in ("opportunity", "quick_costing", "costing_header", "order"):
        record = getattr(invoice, attr, None)
        if not record:
            continue
        if attr == "opportunity" and getattr(record, "pk", None):
            ids.add(record.pk)
        elif getattr(record, "opportunity_id", None):
            ids.add(record.opportunity_id)
    return ids


def _current_category(opportunity):
    if opportunity.is_archived:
        return ARCHIVED_CATEGORY
    stage = opportunity.stage or ""
    if stage in COMPLETED_STAGE_VALUES:
        return COMPLETED_CATEGORY
    if stage in PRODUCTION_STAGE_VALUES:
        return PRODUCTION_CATEGORY
    if stage == AWAITING_PAYMENT_STAGE:
        return AWAITING_PAYMENT_CATEGORY
    if stage in NEGOTIATION_STAGE_VALUES:
        return NEGOTIATION_CATEGORY
    return PROPOSAL_CATEGORY


def _expected_category(*, opportunity, quotation_count, invoice_count, open_invoice_count, production_count, completed_shipment_count):
    if opportunity.is_archived:
        return ARCHIVED_CATEGORY
    if completed_shipment_count:
        return COMPLETED_CATEGORY
    if production_count:
        return PRODUCTION_CATEGORY
    if invoice_count and open_invoice_count:
        return AWAITING_PAYMENT_CATEGORY
    if quotation_count:
        return NEGOTIATION_CATEGORY
    return PROPOSAL_CATEGORY


def _warning(code, opportunity, message, *, severity="warning", target_url=""):
    return {
        "code": code,
        "severity": severity,
        "opportunity_id": getattr(opportunity, "pk", None),
        "opportunity_number": getattr(opportunity, "opportunity_id", "") or f"Opportunity {getattr(opportunity, 'pk', '')}",
        "stage": getattr(opportunity, "stage", ""),
        "message": message,
        "target_url": target_url,
    }


def _global_warning(code, message, *, severity="warning", record_id=None):
    return {
        "code": code,
        "severity": severity,
        "opportunity_id": None,
        "opportunity_number": f"Invoice {record_id}" if record_id else "",
        "record_id": record_id,
        "stage": "",
        "message": message,
        "target_url": "",
    }


def build_opportunity_stage_audit():
    opportunities = list(
        Opportunity.objects.select_related("customer", "lead", "lead__customer", "assigned_to", "lead__assigned_to")
        .order_by("id")
    )
    opportunity_ids = [opportunity.pk for opportunity in opportunities]

    quotation_counts = Counter()
    costing_types_by_opp = defaultdict(set)
    if opportunity_ids:
        for opportunity_id, costing_id, quotation_number in (
            CostingHeader.objects.filter(opportunity_id__in=opportunity_ids, is_archived=False)
            .exclude(quotation_number="")
            .values_list("opportunity_id", "id", "quotation_number")
        ):
            quotation_counts[opportunity_id] += 1
            costing_types_by_opp[opportunity_id].add(f"Advanced Costing #{costing_id}")
        for opportunity_id, quick_id, quotation_number, pricing_type in (
            QuickCosting.objects.filter(opportunity_id__in=opportunity_ids)
            .exclude(quotation_number="")
            .values_list("opportunity_id", "id", "quotation_number", "pricing_type")
        ):
            quotation_counts[opportunity_id] += 1
            label = "Quick Costing"
            if pricing_type:
                label = f"{label} ({pricing_type})"
            costing_types_by_opp[opportunity_id].add(f"{label} #{quick_id}")
        for opportunity_id, costing_id in (
            CostingHeader.objects.filter(opportunity_id__in=opportunity_ids, is_archived=False)
            .filter(quotation_number="")
            .values_list("opportunity_id", "id")
        ):
            costing_types_by_opp[opportunity_id].add(f"Advanced Costing #{costing_id}")
        for opportunity_id, quick_id, pricing_type in (
            QuickCosting.objects.filter(opportunity_id__in=opportunity_ids)
            .filter(quotation_number="")
            .values_list("opportunity_id", "id", "pricing_type")
        ):
            label = "Quick Costing"
            if pricing_type:
                label = f"{label} ({pricing_type})"
            costing_types_by_opp[opportunity_id].add(f"{label} #{quick_id}")

    production_ids_by_opp = defaultdict(list)
    production_ids = []
    for production in ProductionOrder.objects.filter(
        opportunity_id__in=opportunity_ids,
        is_archived=False,
    ).only("id", "opportunity_id", "source_quick_costing_id", "costing_header_id"):
        production_ids_by_opp[production.opportunity_id].append(production.pk)
        production_ids.append(production.pk)

    shipment_ids_by_opp = defaultdict(list)
    completed_shipments_by_opp = defaultdict(list)
    shipment_ids = []
    shipments = Shipment.objects.filter(
        Q(opportunity_id__in=opportunity_ids) | Q(order__opportunity_id__in=opportunity_ids)
    ).select_related("order").only("id", "status", "delivered_at", "opportunity_id", "order__opportunity_id")
    for shipment in shipments:
        opportunity_id = shipment.opportunity_id or getattr(shipment.order, "opportunity_id", None)
        if opportunity_id:
            if shipment.pk not in shipment_ids_by_opp[opportunity_id]:
                shipment_ids_by_opp[opportunity_id].append(shipment.pk)
            shipment_ids.append(shipment.pk)
            if shipment.status == "delivered" or shipment.delivered_at:
                completed_shipments_by_opp[opportunity_id].append(shipment.pk)

    invoices_by_opp = defaultdict(list)
    invoice_by_id = {}
    invoice_link_warnings = []
    invoices = (
        Invoice.objects.select_related(
            "opportunity",
            "quick_costing__opportunity",
            "costing_header__opportunity",
            "order__opportunity",
        )
        .filter(is_archived=False)
        .exclude(status__iexact="cancelled")
        .order_by("id")
    )
    for invoice in invoices:
        invoice_by_id[invoice.pk] = invoice
        resolved_ids = _resolved_opportunity_ids_for_invoice(invoice)
        if not resolved_ids:
            invoice_link_warnings.append(
                _global_warning(
                    "invoice_link_missing",
                    f"{invoice.invoice_number or 'Invoice ' + str(invoice.pk)} has no resolvable opportunity link.",
                    severity="critical",
                    record_id=invoice.pk,
                )
            )
            continue
        if len(resolved_ids) > 1:
            invoice_link_warnings.append(
                _global_warning(
                    "invoice_link_conflict",
                    f"{invoice.invoice_number or 'Invoice ' + str(invoice.pk)} points to multiple opportunity IDs: {', '.join(str(pk) for pk in sorted(resolved_ids))}.",
                    severity="critical",
                    record_id=invoice.pk,
                )
            )
        for opportunity_id in resolved_ids:
            if opportunity_id in opportunity_ids:
                invoices_by_opp[opportunity_id].append(invoice)

    invoice_ids = list(invoice_by_id)
    lifecycles_by_opp = defaultdict(list)
    lifecycles_by_invoice = defaultdict(list)
    lifecycle_filters = Q()
    if opportunity_ids:
        lifecycle_filters |= Q(opportunity_id__in=opportunity_ids)
    if invoice_ids:
        lifecycle_filters |= Q(invoice_id__in=invoice_ids)
    if production_ids:
        lifecycle_filters |= Q(production_order_id__in=production_ids)
    if shipment_ids:
        lifecycle_filters |= Q(shipping_record_id__in=shipment_ids)
    if lifecycle_filters:
        for lifecycle in OrderLifecycle.objects.filter(lifecycle_filters).only(
            "id",
            "opportunity_id",
            "invoice_id",
            "production_order_id",
            "shipping_record_id",
        ):
            if lifecycle.opportunity_id:
                lifecycles_by_opp[lifecycle.opportunity_id].append(lifecycle.pk)
            if lifecycle.invoice_id:
                lifecycles_by_invoice[lifecycle.invoice_id].append(lifecycle.pk)

    rows = []
    warnings = list(invoice_link_warnings)
    category_counts = Counter({category: 0 for category in REPORT_CATEGORIES})
    warning_counts = Counter()
    legacy_test_records = 0

    for opportunity in opportunities:
        invoices = invoices_by_opp.get(opportunity.pk, [])
        open_invoices = [invoice for invoice in invoices if invoice_open_balance(invoice) > 0]
        balance_display = _format_balance_for_invoices(open_invoices)

        quotation_count = quotation_counts[opportunity.pk]
        invoice_count = len({invoice.pk for invoice in invoices})
        open_invoice_count = len({invoice.pk for invoice in open_invoices})
        production_count = len(production_ids_by_opp.get(opportunity.pk, []))
        completed_shipment_count = len(completed_shipments_by_opp.get(opportunity.pk, []))
        current_category = _current_category(opportunity)
        expected_category = _expected_category(
            opportunity=opportunity,
            quotation_count=quotation_count,
            invoice_count=invoice_count,
            open_invoice_count=open_invoice_count,
            production_count=production_count,
            completed_shipment_count=completed_shipment_count,
        )
        category_counts[expected_category] += 1
        try:
            target_url = reverse("opportunity_detail", args=[opportunity.pk])
        except Exception:
            target_url = ""
        salesperson = opportunity.assigned_to or getattr(opportunity.lead, "assigned_to", None)
        legacy_test_reason = _legacy_test_reason(opportunity)
        if legacy_test_reason:
            legacy_test_records += 1

        if not opportunity.customer_id:
            warnings.append(
                _warning(
                    "missing_customer",
                    opportunity,
                    "Opportunity has no direct customer link.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and open_invoice_count and not production_count and opportunity.stage != AWAITING_PAYMENT_STAGE:
            warnings.append(
                _warning(
                    "invoice_stage_incorrect",
                    opportunity,
                    "Invoice exists with outstanding balance, but stage is not Awaiting Payment.",
                    severity="critical",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == AWAITING_PAYMENT_STAGE and not (open_invoice_count and not production_count):
            warnings.append(
                _warning(
                    "awaiting_payment_invalid",
                    opportunity,
                    "Stage is Awaiting Payment but the opportunity does not have an open invoice balance without production.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and production_count and current_category not in {PRODUCTION_CATEGORY, COMPLETED_CATEGORY}:
            warnings.append(
                _warning(
                    "production_stage_incorrect",
                    opportunity,
                    "Production order exists, but opportunity stage is not Production or Completed.",
                    severity="critical",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == "Production" and not production_count:
            warnings.append(
                _warning(
                    "production_link_missing",
                    opportunity,
                    "Opportunity is marked Production but has no linked production order.",
                    severity="critical",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and completed_shipment_count and current_category != COMPLETED_CATEGORY:
            warnings.append(
                _warning(
                    "completed_stage_incorrect",
                    opportunity,
                    "Completed shipment exists, but opportunity is not marked Completed.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == "Proposal" and (quotation_count or invoice_count):
            warnings.append(
                _warning(
                    "proposal_has_downstream_records",
                    opportunity,
                    "Proposal-stage opportunity has a quotation or invoice.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == "Negotiation" and invoice_count:
            warnings.append(
                _warning(
                    "negotiation_has_invoice",
                    opportunity,
                    "Negotiation-stage opportunity already has an invoice.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if production_count > 1:
            warnings.append(
                _warning(
                    "duplicate_production_links",
                    opportunity,
                    f"Opportunity has {production_count} linked production orders.",
                    severity="critical",
                    target_url=target_url,
                )
            )

        rows.append(
            {
                "id": opportunity.pk,
                "opportunity_number": opportunity.opportunity_id,
                "customer": _customer_label(opportunity),
                "current_stage": opportunity.stage,
                "current_category": current_category,
                "expected_category": expected_category,
                "quotation_count": quotation_count,
                "invoice_count": invoice_count,
                "open_invoice_count": open_invoice_count,
                "invoice_ids": sorted({invoice.pk for invoice in invoices}),
                "invoice_numbers": [invoice.invoice_number for invoice in invoices],
                "invoice_statuses": sorted({invoice.status for invoice in invoices}),
                "outstanding_balance": balance_display,
                "production_count": production_count,
                "production_order_ids": production_ids_by_opp.get(opportunity.pk, []),
                "shipment_ids": shipment_ids_by_opp.get(opportunity.pk, []),
                "completed_shipment_count": completed_shipment_count,
                "completed_shipment_ids": completed_shipments_by_opp.get(opportunity.pk, []),
                "lifecycle_ids": sorted(set(lifecycles_by_opp.get(opportunity.pk, []))),
                "assigned_salesperson": _display_user(salesperson),
                "created_date": _date_display(opportunity.created_date),
                "historical_entry_status": _historical_entry_status(opportunity, invoices),
                "historical_entry": _is_historical_entry(opportunity, invoices),
                "legacy_test_status": f"Yes - {legacy_test_reason}" if legacy_test_reason else "No",
                "legacy_test_reason": legacy_test_reason,
                "costing_type": _costing_type_for_opportunity(costing_types_by_opp.get(opportunity.pk)),
                "archived": opportunity.is_archived,
                "customer_missing": not bool(opportunity.customer_id),
                "target_url": target_url,
            }
        )

    for warning in warnings:
        warning_counts[warning["code"]] += 1

    broken_opportunity_ids = {
        warning["opportunity_id"]
        for warning in warnings
        if warning.get("opportunity_id")
    }
    broken_production_links = sum(
        warning_counts[code]
        for code in ("production_stage_incorrect", "production_link_missing", "duplicate_production_links")
    )
    broken_invoice_links = sum(
        warning_counts[code]
        for code in ("invoice_link_missing", "invoice_link_conflict")
    )
    metrics = {
        "total_opportunities": len(rows),
        "workflow_errors": len(warnings),
        "broken_opportunities": len(broken_opportunity_ids),
        "broken_production_links": broken_production_links,
        "broken_invoice_links": broken_invoice_links,
        "legacy_test_records": legacy_test_records,
        "category_counts": dict(category_counts),
        "warning_counts": dict(warning_counts),
    }
    row_by_opp = {row["id"]: row for row in rows}
    detail_records = []
    for warning in warnings:
        invoice = invoice_by_id.get(warning.get("record_id"))
        row = row_by_opp.get(warning.get("opportunity_id"))
        detail_record = _record_from_warning(warning, row=row, invoice=invoice)
        if invoice is not None:
            detail_record.update(
                {
                    "invoice_id": str(invoice.pk),
                    "invoice_status": invoice.status,
                    "outstanding_balance": _format_balance_for_invoices([invoice]),
                    "lifecycle_id": _join_ids(lifecycles_by_invoice.get(invoice.pk, [])),
                    "created_date": _date_display(getattr(invoice, "created_at", None)),
                    "historical_entry_status": (
                        f"Yes - Historical invoice date {_date_display(invoice.invoice_date)}"
                        if getattr(invoice, "is_historical_entry", False)
                        else "No"
                    ),
                }
            )
        detail_records.append(detail_record)
    for row in rows:
        if row.get("legacy_test_reason"):
            detail_records.append(
                _record_from_warning(
                    {
                        "code": "legacy_test_candidate",
                        "severity": "information",
                        "opportunity_id": row["id"],
                        "opportunity_number": row["opportunity_number"],
                        "stage": row["current_stage"],
                        "message": row["legacy_test_reason"],
                        "target_url": row["target_url"],
                    },
                    row=row,
                )
            )
    return {
        "generated_at": timezone.now(),
        "rows": rows,
        "warnings": warnings,
        "detail_records": detail_records,
        "metrics": metrics,
    }


def build_workflow_integrity_dashboard_metrics():
    opportunity_table = Opportunity._meta.db_table
    production_table = ProductionOrder._meta.db_table
    invoice_table = Invoice._meta.db_table
    quick_table = QuickCosting._meta.db_table
    costing_table = CostingHeader._meta.db_table
    shipment_table = Shipment._meta.db_table
    customer_table = "crm_customer"
    lead_table = "crm_lead"
    notification_table = AutomationNotification._meta.db_table
    sql = f"""
        WITH prod_by_opp AS (
            SELECT opportunity_id AS opp_id, COUNT(*) AS prod_count
            FROM {production_table}
            WHERE is_archived = 0 AND opportunity_id IS NOT NULL
            GROUP BY opportunity_id
        ),
        invoice_base AS (
            SELECT id, currency, total_amount, paid_amount, opportunity_id, quick_costing_id, costing_header_id, order_id
            FROM {invoice_table}
            WHERE is_archived = 0 AND LOWER(status) <> 'cancelled'
        ),
        invoice_links AS (
            SELECT id AS invoice_id, opportunity_id AS opp_id, currency, total_amount - paid_amount AS balance
            FROM invoice_base
            WHERE opportunity_id IS NOT NULL
            UNION ALL
            SELECT inv.id AS invoice_id, quick.opportunity_id AS opp_id, inv.currency, inv.total_amount - inv.paid_amount AS balance
            FROM invoice_base inv
            JOIN {quick_table} quick ON quick.id = inv.quick_costing_id
            WHERE quick.opportunity_id IS NOT NULL
            UNION ALL
            SELECT inv.id AS invoice_id, costing.opportunity_id AS opp_id, inv.currency, inv.total_amount - inv.paid_amount AS balance
            FROM invoice_base inv
            JOIN {costing_table} costing ON costing.id = inv.costing_header_id
            WHERE costing.opportunity_id IS NOT NULL
            UNION ALL
            SELECT inv.id AS invoice_id, prod.opportunity_id AS opp_id, inv.currency, inv.total_amount - inv.paid_amount AS balance
            FROM invoice_base inv
            JOIN {production_table} prod ON prod.id = inv.order_id
            WHERE prod.opportunity_id IS NOT NULL
        ),
        distinct_invoice_links AS (
            SELECT DISTINCT invoice_id, opp_id, currency, balance
            FROM invoice_links
            WHERE opp_id IS NOT NULL
        ),
        invoices_by_opp AS (
            SELECT
                opp_id,
                COUNT(DISTINCT invoice_id) AS invoice_count,
                COUNT(DISTINCT CASE WHEN balance > 0 THEN invoice_id END) AS open_invoice_count
            FROM distinct_invoice_links
            GROUP BY opp_id
        ),
        awaiting_links AS (
            SELECT
                opp.id AS opportunity_id,
                COALESCE(opp.customer_id, opp.id) AS customer_key,
                links.currency AS currency,
                links.balance AS balance
            FROM distinct_invoice_links links
            JOIN {opportunity_table} opp ON opp.id = links.opp_id
            LEFT JOIN prod_by_opp prod ON prod.opp_id = opp.id
            WHERE links.balance > 0
              AND opp.is_archived = 0
              AND opp.stage = %s
              AND COALESCE(prod.prod_count, 0) = 0
        ),
        awaiting_summary AS (
            SELECT
                COUNT(DISTINCT opportunity_id) AS opportunity_count,
                COUNT(DISTINCT customer_key) AS customer_count
            FROM awaiting_links
        ),
        awaiting_by_currency AS (
            SELECT
                currency,
                SUM(balance) AS amount
            FROM awaiting_links
            GROUP BY currency
        ),
        quote_links AS (
            SELECT opportunity_id AS opp_id
            FROM {costing_table}
            WHERE is_archived = 0 AND COALESCE(quotation_number, '') <> ''
            UNION ALL
            SELECT opportunity_id AS opp_id
            FROM {quick_table}
            WHERE COALESCE(quotation_number, '') <> ''
        ),
        quotes_by_opp AS (
            SELECT opp_id, COUNT(*) AS quote_count
            FROM quote_links
            WHERE opp_id IS NOT NULL
            GROUP BY opp_id
        ),
        delivered_by_opp AS (
            SELECT opp_id, COUNT(*) AS delivered_count
            FROM (
                SELECT ship.id, COALESCE(ship.opportunity_id, prod.opportunity_id) AS opp_id
                FROM {shipment_table} ship
                LEFT JOIN {production_table} prod ON prod.id = ship.order_id
                WHERE (ship.status = 'delivered' OR ship.delivered_at IS NOT NULL)
            ) delivered
            WHERE opp_id IS NOT NULL
            GROUP BY opp_id
        ),
        invoice_conflicts AS (
            SELECT invoice_id
            FROM distinct_invoice_links
            GROUP BY invoice_id
            HAVING COUNT(DISTINCT opp_id) > 1
        ),
        invoice_broken AS (
            SELECT
                (
                    SELECT COUNT(*)
                    FROM invoice_base inv
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM distinct_invoice_links links
                        WHERE links.invoice_id = inv.id
                    )
                ) + (SELECT COUNT(*) FROM invoice_conflicts) AS broken_invoice_links
        ),
        opp_flags AS (
            SELECT
                opp.id,
                CASE
                    WHEN (
                        LOWER(' ' || COALESCE(opp.opportunity_id, '') || ' ' || COALESCE(opp.notes, '') || ' ' || COALESCE(customer.account_brand, '') || ' ' || COALESCE(customer.contact_name, '') || ' ' || COALESCE(customer.email, '') || ' ' || COALESCE(lead.account_brand, '') || ' ' || COALESCE(lead.contact_name, '') || ' ' || COALESCE(lead.email, '') || ' ') LIKE '%% test %%'
                        OR LOWER(' ' || COALESCE(opp.opportunity_id, '') || ' ' || COALESCE(opp.notes, '') || ' ' || COALESCE(customer.account_brand, '') || ' ' || COALESCE(customer.contact_name, '') || ' ' || COALESCE(customer.email, '') || ' ' || COALESCE(lead.account_brand, '') || ' ' || COALESCE(lead.contact_name, '') || ' ' || COALESCE(lead.email, '') || ' ') LIKE '%% demo %%'
                        OR LOWER(' ' || COALESCE(opp.opportunity_id, '') || ' ' || COALESCE(opp.notes, '') || ' ' || COALESCE(customer.account_brand, '') || ' ' || COALESCE(customer.contact_name, '') || ' ' || COALESCE(customer.email, '') || ' ' || COALESCE(lead.account_brand, '') || ' ' || COALESCE(lead.contact_name, '') || ' ' || COALESCE(lead.email, '') || ' ') LIKE '%% dummy %%'
                        OR LOWER(' ' || COALESCE(opp.opportunity_id, '') || ' ' || COALESCE(opp.notes, '') || ' ' || COALESCE(customer.account_brand, '') || ' ' || COALESCE(customer.contact_name, '') || ' ' || COALESCE(customer.email, '') || ' ' || COALESCE(lead.account_brand, '') || ' ' || COALESCE(lead.contact_name, '') || ' ' || COALESCE(lead.email, '') || ' ') LIKE '%% sandbox %%'
                        OR LOWER(' ' || COALESCE(opp.opportunity_id, '') || ' ' || COALESCE(opp.notes, '') || ' ' || COALESCE(customer.account_brand, '') || ' ' || COALESCE(customer.contact_name, '') || ' ' || COALESCE(customer.email, '') || ' ' || COALESCE(lead.account_brand, '') || ' ' || COALESCE(lead.contact_name, '') || ' ' || COALESCE(lead.email, '') || ' ') LIKE '%% example %%'
                    )
                    THEN 1 ELSE 0
                END AS legacy_test_candidate,
                CASE WHEN opp.customer_id IS NULL THEN 1 ELSE 0 END AS missing_customer,
                CASE WHEN opp.is_archived = 0 AND COALESCE(inv.open_invoice_count, 0) > 0 AND COALESCE(prod.prod_count, 0) = 0 AND opp.stage <> %s THEN 1 ELSE 0 END AS invoice_stage_incorrect,
                CASE WHEN opp.is_archived = 0 AND opp.stage = %s AND NOT (COALESCE(inv.open_invoice_count, 0) > 0 AND COALESCE(prod.prod_count, 0) = 0) THEN 1 ELSE 0 END AS awaiting_payment_invalid,
                CASE WHEN opp.is_archived = 0 AND COALESCE(prod.prod_count, 0) > 0 AND opp.stage NOT IN ('Production', 'Shipment Complete', 'Closed Won') THEN 1 ELSE 0 END AS production_stage_incorrect,
                CASE WHEN opp.is_archived = 0 AND opp.stage = 'Production' AND COALESCE(prod.prod_count, 0) = 0 THEN 1 ELSE 0 END AS production_link_missing,
                CASE WHEN opp.is_archived = 0 AND COALESCE(delivered.delivered_count, 0) > 0 AND opp.stage NOT IN ('Shipment Complete', 'Closed Won') THEN 1 ELSE 0 END AS completed_stage_incorrect,
                CASE WHEN opp.is_archived = 0 AND opp.stage = 'Proposal' AND (COALESCE(inv.invoice_count, 0) > 0 OR COALESCE(quote.quote_count, 0) > 0) THEN 1 ELSE 0 END AS proposal_has_downstream_records,
                CASE WHEN opp.is_archived = 0 AND opp.stage = 'Negotiation' AND COALESCE(inv.invoice_count, 0) > 0 THEN 1 ELSE 0 END AS negotiation_has_invoice,
                CASE WHEN COALESCE(prod.prod_count, 0) > 1 THEN 1 ELSE 0 END AS duplicate_production_links
            FROM {opportunity_table} opp
            LEFT JOIN prod_by_opp prod ON prod.opp_id = opp.id
            LEFT JOIN invoices_by_opp inv ON inv.opp_id = opp.id
            LEFT JOIN quotes_by_opp quote ON quote.opp_id = opp.id
            LEFT JOIN delivered_by_opp delivered ON delivered.opp_id = opp.id
            LEFT JOIN {customer_table} customer ON customer.id = opp.customer_id
            LEFT JOIN {lead_table} lead ON lead.id = opp.lead_id
        )
        SELECT
            COALESCE(SUM(
                missing_customer
                + invoice_stage_incorrect
                + awaiting_payment_invalid
                + production_stage_incorrect
                + production_link_missing
                + completed_stage_incorrect
                + proposal_has_downstream_records
                + negotiation_has_invoice
                + duplicate_production_links
            ), 0) + (SELECT broken_invoice_links FROM invoice_broken) AS workflow_errors,
            COALESCE(SUM(
                CASE WHEN (
                    missing_customer
                    + invoice_stage_incorrect
                    + awaiting_payment_invalid
                    + production_stage_incorrect
                    + production_link_missing
                    + completed_stage_incorrect
                    + proposal_has_downstream_records
                    + negotiation_has_invoice
                    + duplicate_production_links
                ) > 0 THEN 1 ELSE 0 END
            ), 0) AS broken_opportunities,
            COALESCE(SUM(production_stage_incorrect + production_link_missing + duplicate_production_links), 0) AS broken_production_links,
            (SELECT broken_invoice_links FROM invoice_broken) AS broken_invoice_links,
            COALESCE((SELECT opportunity_count FROM awaiting_summary), 0) AS awaiting_payment_count,
            COALESCE((SELECT customer_count FROM awaiting_summary), 0) AS awaiting_payment_customer_count,
            COALESCE((SELECT SUM(amount) FROM awaiting_by_currency WHERE currency = 'CAD'), 0) AS awaiting_cad,
            COALESCE((SELECT SUM(amount) FROM awaiting_by_currency WHERE currency = 'USD'), 0) AS awaiting_usd,
            COALESCE((SELECT SUM(amount) FROM awaiting_by_currency WHERE currency = 'BDT'), 0) AS awaiting_bdt,
            COALESCE(SUM(legacy_test_candidate), 0) AS legacy_test_records,
            (
                SELECT MAX(updated_at)
                FROM {notification_table}
                WHERE source_key = %s
            ) AS last_audit_time
        FROM opp_flags
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                AWAITING_PAYMENT_STAGE,
                AWAITING_PAYMENT_STAGE,
                AWAITING_PAYMENT_STAGE,
                AUDIT_NOTIFICATION_SOURCE_KEY,
            ],
        )
        row = cursor.fetchone() or [0] * 11
    totals = {
        "CAD": {"amount": decimal_or_zero(row[6])},
        "USD": {"amount": decimal_or_zero(row[7])},
        "BDT": {"amount": decimal_or_zero(row[8])},
    }
    totals = {currency: values for currency, values in totals.items() if values["amount"]}
    rows = currency_summary_rows(totals)
    for currency_row in rows:
        currency_row["display"] = format_finance_money(currency_row["amount"], currency_row["currency"])
    display = " / ".join(currency_row["display"] for currency_row in rows) or "-"
    last_audit_time = _parse_database_datetime(row[10])
    workflow_errors = int(row[0] or 0)
    broken_opportunities = int(row[1] or 0)
    broken_production_links = int(row[2] or 0)
    broken_invoice_links = int(row[3] or 0)
    legacy_test_records = int(row[9] or 0)
    return {
        "workflow_errors": workflow_errors,
        "broken_opportunities": broken_opportunities,
        "broken_production_links": broken_production_links,
        "broken_invoice_links": broken_invoice_links,
        "legacy_test_records": legacy_test_records,
        "last_audit_time": last_audit_time,
        "workflow_errors_health": _health_color(workflow_errors),
        "broken_opportunities_health": _health_color(broken_opportunities),
        "broken_production_links_health": _health_color(broken_production_links),
        "broken_invoice_links_health": _health_color(broken_invoice_links),
        "legacy_test_records_health": _health_color(legacy_test_records),
        "awaiting_payment_count": int(row[4] or 0),
        "awaiting_payment_customer_count": int(row[5] or 0),
        "awaiting_payment_rows": rows,
        "awaiting_payment_display": display,
    }


def render_opportunity_stage_audit_markdown(audit):
    generated_at = audit["generated_at"].strftime("%Y-%m-%d %H:%M:%S %Z")
    metrics = audit["metrics"]
    lines = [
        "# Opportunity Stage Audit Report",
        "",
        f"Generated at: {generated_at}",
        "",
        "## Summary",
        "",
        f"- Total opportunities: {metrics['total_opportunities']}",
        f"- Workflow errors: {metrics['workflow_errors']}",
        f"- Broken opportunities: {metrics['broken_opportunities']}",
        f"- Broken production links: {metrics['broken_production_links']}",
        f"- Broken invoice links: {metrics['broken_invoice_links']}",
        "",
        "## Classification Counts",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
    ]
    for category in REPORT_CATEGORIES:
        lines.append(f"| {category} | {metrics['category_counts'].get(category, 0)} |")
    lines.extend(["", "## Warning Counts", "", "| Warning | Count |", "| --- | ---: |"])
    if metrics["warning_counts"]:
        for code, count in sorted(metrics["warning_counts"].items()):
            lines.append(f"| {code} | {count} |")
    else:
        lines.append("| None | 0 |")

    lines.extend(
        [
            "",
            "## Warnings",
            "",
            "| Code | Severity | Opportunity | Current Stage | Message |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if audit["warnings"]:
        for warning in audit["warnings"]:
            lines.append(
                "| {code} | {severity} | {opportunity} | {stage} | {message} |".format(
                    code=warning["code"],
                    severity=warning["severity"],
                    opportunity=warning["opportunity_number"] or "-",
                    stage=warning["stage"] or "-",
                    message=warning["message"].replace("|", "\\|"),
                )
            )
    else:
        lines.append("| None | - | - | - | No warnings found. |")

    lines.extend(
        [
            "",
            "## Opportunity Classification",
            "",
            "| ID | Opportunity | Customer | Current Stage | Current Classification | Expected Classification | Quotations | Invoices | Open Invoices | Outstanding Balance | Production Orders | Completed Shipments | Archived |",
            "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for row in audit["rows"]:
        lines.append(
            "| {id} | {opportunity_number} | {customer} | {current_stage} | {current_category} | {expected_category} | {quotation_count} | {invoice_count} | {open_invoice_count} | {outstanding_balance} | {production_count} | {completed_shipment_count} | {archived} |".format(
                **{
                    key: str(value).replace("|", "\\|")
                    for key, value in row.items()
                    if key
                    in {
                        "id",
                        "opportunity_number",
                        "customer",
                        "current_stage",
                        "current_category",
                        "expected_category",
                        "quotation_count",
                        "invoice_count",
                        "open_invoice_count",
                        "outstanding_balance",
                        "production_count",
                        "completed_shipment_count",
                        "archived",
                    }
                }
            )
        )
    lines.append("")
    return "\n".join(lines)


def _markdown_value(value):
    text = str(value if value is not None else "")
    return text.replace("\n", " ").replace("|", "\\|")


def _detail_records_for_section(audit, section_key):
    return [record for record in audit.get("detail_records", []) if record["section"] == section_key]


def render_crm_data_integrity_details_markdown(audit):
    generated_at = audit["generated_at"].strftime("%Y-%m-%d %H:%M:%S %Z")
    metrics = audit["metrics"]
    lines = [
        "# CRM Data Integrity Details",
        "",
        f"Generated at: {generated_at}",
        "",
        "Reporting-only audit. No opportunity, invoice, payment, production, accounting, or shipment records were repaired by this report.",
        "",
        "## Summary",
        "",
        f"- Workflow Errors: {metrics['workflow_errors']}",
        f"- Broken Opportunities: {metrics['broken_opportunities']}",
        f"- Broken Production Links: {metrics['broken_production_links']}",
        f"- Broken Invoice Links: {metrics['broken_invoice_links']}",
        f"- Legacy Test Records: {metrics.get('legacy_test_records', 0)}",
        "",
        "## Dry-Run Repair Commands",
        "",
        "These commands are reporting-only by default. Do not execute repair actions without CEO/Admin approval.",
        "",
        "```bash",
        "python manage.py repair_opportunity_stages --dry-run",
        "python manage.py repair_invoice_links --dry-run",
        "python manage.py repair_production_links --dry-run",
        "python manage.py repair_shipment_completion --dry-run",
        "```",
        "",
        "## Repair Classifications",
        "",
        f"- {REPAIR_SAFE_AUTO_FIX}: likely mechanical repair after approval.",
        f"- {REPAIR_MANUAL_REVIEW}: human review required before any data change.",
        f"- {REPAIR_IGNORE_LEGACY_TEST}: likely legacy/test record; exclude from operational repair unless confirmed real.",
        "",
    ]
    headers = [
        "Opportunity ID",
        "Opportunity Number",
        "Customer Name",
        "Current Stage",
        "Expected Stage",
        "Invoice ID",
        "Invoice Status",
        "Outstanding Balance",
        "Production Order ID",
        "Shipment ID",
        "Lifecycle ID",
        "Assigned Salesperson",
        "Created Date",
        "Historical Entry",
        "Legacy Test",
        "Repair Class",
        "Reason for Failure",
        "Recommended Repair Action",
    ]
    for section_key, section_title in DETAIL_REPORT_SECTIONS:
        records = _detail_records_for_section(audit, section_key)
        lines.extend([f"## {section_title}", ""])
        if not records:
            lines.extend(["No records found.", ""])
            continue
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for record in records:
            values = [
                record["opportunity_id"],
                record["opportunity_number"],
                record["customer_name"],
                record["current_stage"],
                record["expected_stage"],
                record["invoice_id"],
                record["invoice_status"],
                record["outstanding_balance"],
                record["production_order_id"],
                record["shipment_id"],
                record["lifecycle_id"],
                record["assigned_salesperson"],
                record["created_date"],
                record["historical_entry_status"],
                record["legacy_test_status"],
                record["repair_classification"],
                record["reason_for_failure"],
                record["recommended_repair_action"],
            ]
            lines.append("| " + " | ".join(_markdown_value(value) for value in values) + " |")
        lines.append("")
    return "\n".join(lines)


def render_crm_integrity_csv(audit, *, filter_mode="broken"):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for record in _filter_detail_records(audit.get("detail_records", []), filter_mode):
        writer.writerow({column: record.get(column, "") for column in CSV_COLUMNS})
    return output.getvalue()


def write_opportunity_stage_audit_report(path, audit=None):
    audit = audit or build_opportunity_stage_audit()
    output_path = Path(path)
    output_path.write_text(render_opportunity_stage_audit_markdown(audit), encoding="utf-8")
    return output_path


def write_crm_data_integrity_details(path, audit=None):
    audit = audit or build_opportunity_stage_audit()
    output_path = Path(path)
    output_path.write_text(render_crm_data_integrity_details_markdown(audit), encoding="utf-8")
    return output_path


def write_crm_integrity_csv(path, audit=None, *, filter_mode="broken"):
    audit = audit or build_opportunity_stage_audit()
    output_path = Path(path)
    output_path.write_text(render_crm_integrity_csv(audit, filter_mode=filter_mode), encoding="utf-8")
    return output_path


def build_repair_command_preview(command_name, *, filter_codes=None):
    audit = build_opportunity_stage_audit()
    records = audit.get("detail_records", [])
    if filter_codes:
        records = [record for record in records if record["warning_code"] in filter_codes]
    records = [record for record in records if record["repair_classification"] != REPAIR_IGNORE_LEGACY_TEST]
    return {
        "command": command_name,
        "dry_run": True,
        "records": records,
        "count": len(records),
        "generated_at": audit["generated_at"],
    }


def sync_opportunity_stage_audit_notification(audit):
    metrics = audit["metrics"]
    source_key = AUDIT_NOTIFICATION_SOURCE_KEY
    queryset = AutomationNotification.objects.filter(source_key=source_key)
    if metrics["workflow_errors"] <= 0:
        queryset.update(is_resolved=True, resolved_at=timezone.now())
        return {"active": False, "source_key": source_key}

    content_type = ContentType.objects.get_for_model(Opportunity, for_concrete_model=False)
    first_warning = next((warning for warning in audit["warnings"] if warning.get("opportunity_id")), None)
    record_object_id = first_warning["opportunity_id"] if first_warning else None
    target_url = first_warning["target_url"] if first_warning else reverse("ceo_dashboard")
    priority = "critical" if metrics["broken_production_links"] or metrics["broken_invoice_links"] else "high"
    AutomationNotification.objects.update_or_create(
        source_key=source_key,
        defaults={
            "rule": None,
            "rule_type": "general",
            "notification_type": "general",
            "title": "Opportunity workflow integrity warning",
            "message": (
                f"{metrics['workflow_errors']} workflow warning(s) found across "
                f"{metrics['broken_opportunities']} opportunity record(s). "
                f"Production link warnings: {metrics['broken_production_links']}. "
                f"Invoice link warnings: {metrics['broken_invoice_links']}."
            ),
            "priority": priority,
            "is_read": False,
            "is_resolved": False,
            "resolved_at": None,
            "record_content_type": content_type if record_object_id else None,
            "record_object_id": record_object_id,
            "record_label": first_warning["opportunity_number"] if first_warning else "Opportunity Stage Audit",
            "target_url": target_url,
            "assigned_user": None,
            "assigned_role": "CEO",
            "due_date": timezone.localdate(),
        },
    )
    return {"active": True, "source_key": source_key}
