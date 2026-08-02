from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum

from crm.models import AccountingEntry, Invoice, InvoicePayment
from crm.services.historical_receivables import build_historical_ledger_plan


@dataclass(frozen=True)
class FinancialHistoricalException:
    severity: str
    area: str
    record: str
    current_value: str
    expected_value: str
    difference: str
    reason: str
    evidence_available: str
    recommended_action: str
    approval_required: str = "CEO or Finance"


def _amount(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _entry_evidence(entry):
    evidence = []
    if entry.production_order_id:
        evidence.append(f"production order {entry.production_order_id}")
    if entry.opportunity_id:
        evidence.append(f"opportunity {entry.opportunity_id}")
    if entry.customer_id:
        evidence.append(f"customer {entry.customer_id}")
    if entry.description:
        evidence.append("description")
    if entry.internal_note:
        evidence.append("internal note")
    try:
        if entry.attachments.exists():
            evidence.append("attachment")
    except Exception:
        pass
    return ", ".join(evidence) or "No structured supporting evidence link"


def build_financial_historical_exception_plan():
    exceptions = []
    ar_plan = build_historical_ledger_plan()
    for row in ar_plan.exceptions:
        exceptions.append(
            FinancialHistoricalException(
                severity="CRITICAL" if row.blocking else "HIGH",
                area="Accounts Receivable",
                record=f"Invoice {row.invoice}",
                current_value=f"{row.amount} {row.currency}; exception {row.code}",
                expected_value="Invoice principal, receipt events, and allocations supported by consistent source records",
                difference="See exception amount; exact correction requires evidence",
                reason=row.reason,
                evidence_available=row.source or f"Invoice and payment history; customer {row.customer}",
                recommended_action=row.recommended_action,
            )
        )

    payable_candidates = (
        AccountingEntry.objects.filter(direction=AccountingEntry.DIR_OUT)
        .exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .select_related("customer", "opportunity", "production_order")
        .prefetch_related("attachments")
        .order_by("date", "id")
    )
    for entry in payable_candidates:
        exceptions.append(
            FinancialHistoricalException(
                severity="HIGH",
                area="Accounts Payable",
                record=f"AccountingEntry {entry.pk}",
                current_value=f"{_amount(entry.amount_original)} {(entry.currency or '').upper()} / status {entry.status or 'blank'}",
                expected_value="Approved supplier bill, due date, supplier identity, and payment allocations",
                difference="Not computable from legacy row",
                reason="Legacy outbound entry does not prove whether this is an unpaid bill, a bill payment, an immediate expense, or a transfer.",
                evidence_available=_entry_evidence(entry),
                recommended_action="Finance must classify the row and link documentary evidence before creating an opening payable or historical bill/payment.",
            )
        )

    for invoice in Invoice.objects.filter(
        Q(currency="USD") | Q(currency="BDT")
    ).exclude(status="cancelled").order_by("id"):
        if hasattr(invoice, "financial_state") and invoice.financial_state.rate_to_cad > 0 and invoice.financial_state.rate_to_bdt > 0:
            continue
        exceptions.append(
            FinancialHistoricalException(
                severity="HIGH",
                area="Currency",
                record=f"Invoice {invoice.pk}",
                current_value=f"{_amount(invoice.total_amount)} {invoice.currency}; no immutable invoice conversion snapshot",
                expected_value="Evidence-backed rate_to_cad and rate_to_bdt on effective invoice date",
                difference="Missing rate snapshot",
                reason="The invoice predates the Financial Core snapshot contract.",
                evidence_available=f"Invoice effective date {invoice.effective_invoice_date}",
                recommended_action="Locate dated bank/central-bank evidence and approve the historical rate; otherwise leave in the manual review queue.",
            )
        )

    for payment in InvoicePayment.objects.filter(
        Q(rate_to_cad__lte=0) | Q(rate_to_bdt__lte=0)
    ).select_related("invoice").order_by("id"):
        missing = []
        if payment.rate_to_cad <= 0:
            missing.append("CAD")
        if payment.rate_to_bdt <= 0:
            missing.append("BDT")
        exceptions.append(
            FinancialHistoricalException(
                severity="HIGH",
                area="Currency",
                record=f"InvoicePayment {payment.pk}",
                current_value=f"{_amount(payment.amount)} {payment.currency}; missing {'/'.join(missing)} rate",
                expected_value="Complete immutable payment conversion snapshot",
                difference="Missing rate snapshot",
                reason="A required historical conversion is zero or absent.",
                evidence_available=f"Payment date {payment.payment_date}; invoice {payment.invoice_id}; accounting entry {payment.accounting_entry_id or 'none'}",
                recommended_action="Resolve only from dated payment/bank evidence and then reconcile the receipt event.",
            )
        )

    cash_rows = (
        AccountingEntry.objects.exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .values("side", "currency")
        .annotate(
            inflow=Sum("amount_original", filter=Q(direction=AccountingEntry.DIR_IN)),
            outflow=Sum("amount_original", filter=Q(direction=AccountingEntry.DIR_OUT)),
        )
        .order_by("side", "currency")
    )
    for row in cash_rows:
        net = _amount((row["inflow"] or 0) - (row["outflow"] or 0))
        exceptions.append(
            FinancialHistoricalException(
                severity="CRITICAL",
                area="Cash and Bank Opening",
                record=f"Legacy aggregate {row['side'] or 'UNSET'} {row['currency'] or 'UNSET'}",
                current_value=f"Net legacy movement {net} {row['currency'] or ''}",
                expected_value="Approved opening statement balance by named cash/bank account",
                difference="Cannot reconcile aggregate movement to a bank statement",
                reason="Legacy entries are not assigned to canonical cash/bank accounts and may omit opening balances.",
                evidence_available="Legacy entry dates, directions, descriptions, and any attachments",
                recommended_action="Finance must supply statement opening/closing balances and map each instrument before an opening-balance journal is approved.",
            )
        )

    for area, expected, action in (
        ("Owner Equity", "Approved owner investment, withdrawal, and retained earnings opening balances", "Review corporate records and bank evidence; post approved opening journals only."),
        ("Loans", "Principal by lender, currency, issue date, and unpaid balance", "Review loan agreements and split principal from interest before posting."),
        ("Inventory", "Approved inventory valuation by location and as-of date", "Perform/approve an inventory valuation; keep current balance sheet inventory marked estimated until then."),
        ("Taxes", "Input tax, output tax, paid tax, and taxes payable control balances", "Reconcile filed returns and payment evidence before opening tax balances."),
    ):
        exceptions.append(
            FinancialHistoricalException(
                severity="CRITICAL",
                area=area,
                record="Opening balance control",
                current_value="No canonical opening subledger/control balance",
                expected_value=expected,
                difference="Unknown",
                reason=f"Legacy text classifications do not establish a reliable {area.lower()} opening balance.",
                evidence_available="Legacy accounting entries and external records, if supplied",
                recommended_action=action,
            )
        )
    return exceptions


def render_financial_exception_report(exceptions):
    from crm.services.financial_exception_review import exception_evidence_needed, exception_identifier

    counts = {severity: sum(1 for row in exceptions if row.severity == severity) for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    lines = [
        "# Historical Financial Exception Report",
        "",
        "## Rehearsal status",
        "",
        "This report is generated from a database-copy rehearsal. It does not correct source records and does not create guessed accounting transactions.",
        "",
        f"- Critical: {counts['CRITICAL']}",
        f"- High: {counts['HIGH']}",
        f"- Medium: {counts['MEDIUM']}",
        f"- Low: {counts['LOW']}",
        f"- Total: {len(exceptions)}",
        "",
        "## Exceptions",
        "",
        "All generated exceptions are initially classified as **Evidence required**. Review decisions are recorded in the restricted Exception Review Center; this file is a copied-database rehearsal artifact.",
        "",
        "| Exception ID | Severity | Area | Record | Current value | Expected value | Difference | Reason | Evidence available | Evidence needed | Recommended action | Review status | Approval required |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in exceptions:
        values = [
            exception_identifier(row),
            row.severity,
            row.area,
            row.record,
            row.current_value,
            row.expected_value,
            row.difference,
            row.reason,
            row.evidence_available,
            exception_evidence_needed(row),
            row.recommended_action,
            "Evidence required",
            row.approval_required,
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "Historical Financial Core activation remains blocked until every Critical exception is resolved or explicitly approved as an opening-balance exception, AR/AP control accounts reconcile, and cash/bank statements reconcile.",
        ]
    )
    return "\n".join(lines) + "\n"
