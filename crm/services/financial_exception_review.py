import hashlib
import json
import re
from dataclasses import asdict

from django.db import transaction

from crm.models import AccountingEntry, FinancialExceptionReview, Invoice, InvoicePayment
from crm.services.financial_historical_reconciliation import build_financial_historical_exception_plan


EVIDENCE_BY_AREA = {
    "Accounts Receivable": "Customer invoice, customer statement, payment receipt, and matching bank statement.",
    "Accounts Payable": "Supplier bill, supplier statement, payment proof, and matching bank statement.",
    "Currency": "Dated bank conversion record or approved central-bank rate for the transaction date.",
    "Cash and Bank Opening": "Named account statement showing the approved opening date and balance.",
    "Inventory": "Approved physical inventory count and valuation report as of the opening date.",
    "Owner Equity": "Corporate equity record, owner authorization, and matching bank evidence.",
    "Loans": "Executed loan agreement and lender statement separating principal and interest.",
    "Taxes": "Filed tax return, assessment, payment receipt, and tax-account reconciliation.",
    "Fixed Assets": "Asset purchase document, proof of payment, ownership record, and opening asset register.",
}


def exception_evidence_needed(exception):
    return EVIDENCE_BY_AREA.get(
        exception.area,
        "Source document, payment evidence, and an approved reconciliation supporting the proposed value.",
    )


def exception_source_key(exception):
    payload = json.dumps(asdict(exception), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def exception_identifier(exception):
    return f"FEX-{exception_source_key(exception)[:12].upper()}"


def _source_details(exception):
    record_type, _, raw_identifier = exception.record.partition(" ")
    identifier = raw_identifier.strip()
    details = {
        "record_type": record_type or "Control",
        "record_number": identifier or exception.record,
        "customer": None,
        "supplier": None,
        "counterparty_name": "",
        "transaction_date": None,
        "side": "",
        "currency": "",
    }
    source = None
    if record_type == "Invoice":
        source = Invoice.objects.select_related("customer").filter(invoice_number=identifier).first()
        if source is None and identifier.isdigit():
            source = Invoice.objects.select_related("customer").filter(pk=int(identifier)).first()
        if source:
            details.update(
                record_number=source.invoice_number,
                customer=source.customer,
                counterparty_name=str(source.customer or ""),
                transaction_date=source.effective_invoice_date,
                side=source.invoice_region,
                currency=source.currency,
            )
    elif record_type == "InvoicePayment" and identifier.isdigit():
        source = InvoicePayment.objects.select_related("invoice__customer").filter(pk=int(identifier)).first()
        if source:
            details.update(
                customer=source.invoice.customer,
                counterparty_name=str(source.invoice.customer or ""),
                transaction_date=source.payment_date,
                side=source.invoice.invoice_region,
                currency=source.currency,
            )
    elif record_type == "AccountingEntry" and identifier.isdigit():
        source = AccountingEntry.objects.select_related("customer").filter(pk=int(identifier)).first()
        if source:
            details.update(
                customer=source.customer,
                counterparty_name=str(source.customer or ""),
                transaction_date=source.date,
                side=source.side,
                currency=source.currency,
            )
    else:
        currency_match = re.search(r"\b(CAD|USD|BDT)\b", exception.record + " " + exception.current_value)
        if currency_match:
            details["currency"] = currency_match.group(1)
        side_match = re.search(r"\b(CA|BD)\b", exception.record)
        if side_match:
            details["side"] = side_match.group(1)
    return details


def _exception_type(exception):
    if "exception " in exception.current_value:
        return exception.current_value.rsplit("exception ", 1)[-1].strip()[:100]
    return exception.area.upper().replace(" ", "_")[:100]


def build_exception_review_rows():
    rows = []
    for exception in build_financial_historical_exception_plan():
        details = _source_details(exception)
        rows.append(
            {
                "exception_id": exception_identifier(exception),
                "source_key": exception_source_key(exception),
                "severity": exception.severity,
                "area": exception.area,
                "exception_type": _exception_type(exception),
                **details,
                "current_value": exception.current_value,
                "expected_value": exception.expected_value,
                "difference": exception.difference,
                "reason": exception.reason,
                "evidence_available": exception.evidence_available,
                "evidence_needed": exception_evidence_needed(exception),
                "recommended_action": exception.recommended_action,
                "review_status": FinancialExceptionReview.STATUS_EVIDENCE_REQUIRED,
                "source_snapshot": asdict(exception),
            }
        )
    return rows


@transaction.atomic
def load_exception_reviews(*, actor, apply=False):
    rows = build_exception_review_rows()
    existing = set(
        FinancialExceptionReview.objects.filter(source_key__in=[row["source_key"] for row in rows]).values_list(
            "source_key", flat=True
        )
    )
    missing = [row for row in rows if row["source_key"] not in existing]
    if apply:
        FinancialExceptionReview.objects.bulk_create(
            [
                FinancialExceptionReview(
                    **row,
                    created_by=actor,
                    modified_by=actor,
                    change_reason="Imported from copied-database historical exception rehearsal.",
                )
                for row in missing
            ]
        )
    return {
        "planned": len(rows),
        "existing": len(existing),
        "created": len(missing) if apply else 0,
        "would_create": len(missing),
        "critical": sum(row["severity"] == FinancialExceptionReview.SEVERITY_CRITICAL for row in rows),
        "high": sum(row["severity"] == FinancialExceptionReview.SEVERITY_HIGH for row in rows),
    }
