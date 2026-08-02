from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import (
    CashBankAccount,
    Invoice,
    InvoiceFinancialState,
    JournalEntry,
    ReceivableAllocation,
    ReceivableEvent,
)
from crm.services.financial_currency import CurrencySnapshot, money, resolve_currency_snapshot
from crm.services.financial_journal import JournalLineSpec, create_draft_journal, post_journal, reverse_journal
from crm.services.invoice_state import canonical_approval_status, canonical_document_status
from crm.services.receivables_ledger import (
    DuplicateLedgerWrite,
    create_draft_allocation,
    create_draft_event,
    post_allocation,
    post_event,
)


class ReceivableAccountingError(ValueError):
    pass


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ReceivableAccountingError("An authenticated actor is required.")
    return actor


def resolve_legacy_payment_account(*, side, currency, payment_method):
    kind = {
        "cash": CashBankAccount.KIND_CASH,
        "mobile": CashBankAccount.KIND_MOBILE,
    }.get((payment_method or "").lower(), CashBankAccount.KIND_BANK)
    candidates = list(
        CashBankAccount.objects.filter(
            side=(side or "").upper(),
            currency=(currency or "").upper(),
            kind=kind,
            is_active=True,
        )[:2]
    )
    if len(candidates) != 1:
        raise ReceivableAccountingError(
            "Financial Core dual-write requires exactly one active matching cash/bank account; no account was guessed."
        )
    return candidates[0]


def _invoice_revenue_key(invoice):
    if invoice.invoice_type == "sample":
        return "SAMPLE_REVENUE"
    if invoice.invoice_type == "sewing_charge":
        return "CMT_REVENUE"
    quick = invoice.quick_costing
    pricing_type = (getattr(quick, "effective_pricing_type", "") or "").lower()
    return {
        "full_package": "FULL_PACKAGE_REVENUE",
        "fob": "FOB_REVENUE",
        "door_to_door": "DOOR_TO_DOOR_REVENUE",
        "cmt_sewing": "CMT_REVENUE",
    }.get(pricing_type, "PRODUCT_SALES")


def _invoice_credit_lines(invoice, total):
    tax = money(invoice.tax_amount or 0)
    shipping = money(invoice.shipping_amount or 0)
    product = money(total - tax - shipping)
    if product < 0:
        raise ReceivableAccountingError("Invoice tax and shipping cannot exceed the invoice total.")
    lines = []
    if product:
        lines.append(
            JournalLineSpec(
                _invoice_revenue_key(invoice),
                credit=product,
                description=f"Invoice {invoice.invoice_number} operating revenue",
                customer=invoice.customer,
                production_order=invoice.order,
            )
        )
    if shipping:
        lines.append(
            JournalLineSpec(
                "SHIPPING_REVENUE",
                credit=shipping,
                description=f"Invoice {invoice.invoice_number} shipping",
                customer=invoice.customer,
                production_order=invoice.order,
            )
        )
    if tax:
        lines.append(
            JournalLineSpec(
                "TAXES_PAYABLE",
                credit=tax,
                description=f"Invoice {invoice.invoice_number} tax",
                customer=invoice.customer,
            )
        )
    return lines


def invoice_outstanding(invoice) -> Decimal:
    state = getattr(invoice, "financial_state", None)
    if not state or state.document_status != InvoiceFinancialState.DOCUMENT_ISSUED:
        return Decimal("0")
    allocated = (
        ReceivableAllocation.objects.filter(
            invoice=invoice,
            state=ReceivableAllocation.STATE_POSTED,
        ).aggregate(total=Sum("signed_amount"))["total"]
        or Decimal("0")
    )
    return max(money(state.issued_native_amount - allocated), Decimal("0"))


def _sync_payment_status(invoice):
    state = InvoiceFinancialState.objects.select_for_update().get(invoice=invoice)
    if state.document_status == InvoiceFinancialState.DOCUMENT_VOIDED:
        return state
    allocated = (
        ReceivableAllocation.objects.filter(
            invoice=invoice,
            state=ReceivableAllocation.STATE_POSTED,
        ).aggregate(total=Sum("signed_amount"))["total"]
        or Decimal("0")
    )
    if allocated <= 0:
        payment_status = InvoiceFinancialState.PAYMENT_UNPAID
    elif allocated < state.issued_native_amount:
        payment_status = InvoiceFinancialState.PAYMENT_PARTIAL
    else:
        payment_status = InvoiceFinancialState.PAYMENT_SETTLED
    InvoiceFinancialState.objects.filter(pk=state.pk).update(
        payment_status=payment_status,
        modified_at=timezone.now(),
    )
    state.refresh_from_db()
    return state


@transaction.atomic
def issue_invoice_to_financial_core(
    invoice,
    *,
    actor,
    rate_to_cad=None,
    rate_to_bdt=None,
    evidence_reference="",
) -> InvoiceFinancialState:
    approver = _actor(actor)
    locked = Invoice.objects.select_for_update().select_related(
        "customer", "order", "quick_costing"
    ).get(pk=invoice.pk)
    if not locked.customer_id:
        raise ReceivableAccountingError("An issued invoice requires a customer.")
    if canonical_document_status(locked) == "VOIDED":
        raise ReceivableAccountingError("A voided invoice cannot be issued.")
    if canonical_approval_status(locked) != "APPROVED":
        raise ReceivableAccountingError("Only an approved invoice can be issued to the Financial Core.")
    total = money(locked.total_amount)
    if total <= 0:
        raise ReceivableAccountingError("An issued invoice total must be greater than zero.")

    existing = InvoiceFinancialState.objects.filter(invoice=locked).first()
    if existing and existing.document_status == InvoiceFinancialState.DOCUMENT_ISSUED:
        return existing
    if existing and existing.document_status == InvoiceFinancialState.DOCUMENT_VOIDED:
        raise ReceivableAccountingError("A voided Financial Core invoice must be reissued as a new invoice.")

    snapshot = resolve_currency_snapshot(
        native_amount=total,
        currency=locked.currency,
        transaction_date=locked.effective_invoice_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    journal = create_draft_journal(
        journal_date=locked.effective_invoice_date,
        reference=f"FIN-INV-{locked.invoice_number}",
        description=f"Issue customer invoice {locked.invoice_number}",
        side=(locked.invoice_region or "BD" if locked.invoice_market == "bangladesh" else "CA"),
        snapshot=snapshot,
        source_key=f"INVOICE:{locked.pk}:ISSUED",
        source_record=locked,
        actor=approver,
        lines=[
            JournalLineSpec(
                "ACCOUNTS_RECEIVABLE",
                debit=total,
                description=f"Invoice {locked.invoice_number}",
                customer=locked.customer,
                production_order=locked.order,
            ),
            *_invoice_credit_lines(locked, total),
        ],
    )
    journal = post_journal(journal, actor=approver)
    event = create_draft_event(
        customer=locked.customer,
        source_invoice=locked,
        kind=ReceivableEvent.KIND_INVOICE_ISSUED,
        event_date=locked.effective_invoice_date,
        effective_date=locked.effective_invoice_date,
        native_amount=total,
        currency=locked.currency,
        rate_to_cad=snapshot.rate_to_cad,
        rate_to_bdt=snapshot.rate_to_bdt,
        idempotency_key=f"CORE:INVOICE:{locked.pk}:ISSUED",
        external_reference=locked.invoice_number,
        evidence_reference=(evidence_reference or locked.invoice_number).strip(),
        financial_journal=journal,
        actor=approver,
    )
    event = post_event(event, actor=approver)
    state, _created = InvoiceFinancialState.objects.update_or_create(
        invoice=locked,
        defaults={
            "document_status": InvoiceFinancialState.DOCUMENT_ISSUED,
            "approval_status": InvoiceFinancialState.APPROVAL_APPROVED,
            "payment_status": InvoiceFinancialState.PAYMENT_UNPAID,
            "issued_date": locked.effective_invoice_date,
            "currency": snapshot.currency,
            "issued_native_amount": snapshot.native_amount,
            "rate_to_cad": snapshot.rate_to_cad,
            "rate_to_bdt": snapshot.rate_to_bdt,
            "issued_amount_cad": snapshot.amount_cad,
            "issued_amount_bdt": snapshot.amount_bdt,
            "receivable_journal": journal,
            "receivable_event": event,
            "approved_by": approver,
            "approved_at": timezone.now(),
            "modified_by": approver,
            "created_by": approver,
        },
    )
    return state


@transaction.atomic
def void_financial_core_invoice(invoice, *, actor, reason, void_date=None):
    approver = _actor(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ReceivableAccountingError("A void reason is required.")
    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    state = InvoiceFinancialState.objects.select_for_update().select_related(
        "receivable_journal", "receivable_event"
    ).get(invoice=locked)
    if state.document_status != InvoiceFinancialState.DOCUMENT_ISSUED:
        raise ReceivableAccountingError("Only an issued Financial Core invoice can be voided.")
    allocation_total = (
        ReceivableAllocation.objects.filter(
            invoice=locked, state=ReceivableAllocation.STATE_POSTED
        ).aggregate(total=Sum("signed_amount"))["total"]
        or Decimal("0")
    )
    if allocation_total:
        raise ReceivableAccountingError("Allocated invoices require payment/credit reversals before voiding.")
    reversal_journal = reverse_journal(
        state.receivable_journal,
        actor=approver,
        reason=reason,
        reversal_date=void_date or timezone.localdate(),
    )
    reversal_event = create_draft_event(
        customer=locked.customer,
        source_invoice=locked,
        kind=ReceivableEvent.KIND_REVERSAL,
        event_date=reversal_journal.journal_date,
        effective_date=reversal_journal.journal_date,
        native_amount=state.issued_native_amount,
        currency=state.currency,
        rate_to_cad=state.rate_to_cad,
        rate_to_bdt=state.rate_to_bdt,
        idempotency_key=f"CORE:INVOICE:{locked.pk}:VOID",
        external_reference=reversal_journal.reference,
        reason=reason,
        evidence_reference=reason,
        financial_journal=reversal_journal,
        reverses_event=state.receivable_event,
        actor=approver,
    )
    post_event(reversal_event, actor=approver)
    ReceivableEvent.objects.filter(pk=state.receivable_event_id).update(state=ReceivableEvent.STATE_REVERSED)
    InvoiceFinancialState.objects.filter(pk=state.pk).update(
        document_status=InvoiceFinancialState.DOCUMENT_VOIDED,
        payment_status=InvoiceFinancialState.PAYMENT_UNPAID,
        change_reason=reason,
        modified_by=approver,
        modified_at=timezone.now(),
    )
    state.refresh_from_db()
    return state


def _allocation_pairs(invoices, amount):
    remaining = money(amount)
    pairs = []
    for invoice in invoices:
        outstanding = invoice_outstanding(invoice)
        applied = min(outstanding, remaining)
        if applied > 0:
            pairs.append((invoice, applied))
            remaining -= applied
        if remaining <= 0:
            break
    return pairs, remaining


@transaction.atomic
def record_customer_receipt(
    *,
    customer,
    amount,
    currency,
    receipt_date,
    payment_account,
    reference,
    actor,
    invoices=(),
    payment_method="bank",
    rate_to_cad=None,
    rate_to_bdt=None,
    evidence_reference="",
    legacy_payment=None,
    legacy_accounting_entry=None,
):
    approver = _actor(actor)
    if not reference or not str(reference).strip():
        raise ReceivableAccountingError("Customer receipts require an external reference.")
    locked_invoices = list(
        Invoice.objects.select_for_update()
        .select_related("financial_state")
        .filter(pk__in=[invoice.pk for invoice in invoices])
        .order_by("due_date", "id")
    )
    for invoice in locked_invoices:
        if invoice.customer_id != customer.pk:
            raise ReceivableAccountingError("Every allocated invoice must belong to the receipt customer.")
        if (invoice.currency or "").upper() != (currency or "").upper():
            raise ReceivableAccountingError("Receipt and allocated invoices must use the same original currency.")
        if not hasattr(invoice, "financial_state") or invoice.financial_state.document_status != InvoiceFinancialState.DOCUMENT_ISSUED:
            raise ReceivableAccountingError("Allocated invoices must already be issued in the Financial Core.")
    snapshot = resolve_currency_snapshot(
        native_amount=amount,
        currency=currency,
        transaction_date=receipt_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=legacy_payment,
        actor=approver,
    )
    if payment_account.currency != snapshot.currency:
        raise ReceivableAccountingError(
            "Receipt currency must match the cash/bank account currency; record an evidenced company transfer separately."
        )
    pairs, unallocated = _allocation_pairs(locked_invoices, snapshot.native_amount)
    allocated = snapshot.native_amount - unallocated
    credit_lines = []
    if allocated:
        credit_lines.append(
            JournalLineSpec(
                "ACCOUNTS_RECEIVABLE",
                credit=allocated,
                customer=customer,
                description=f"Customer receipt {reference} allocated",
            )
        )
    if unallocated:
        credit_lines.append(
            JournalLineSpec(
                "CUSTOMER_DEPOSITS",
                credit=unallocated,
                customer=customer,
                description=f"Customer receipt {reference} unapplied credit",
            )
        )
    journal = create_draft_journal(
        journal_date=receipt_date,
        reference=f"FIN-RCPT-{reference}",
        description=f"Customer receipt {reference}",
        side=payment_account.side,
        snapshot=snapshot,
        source_key=f"CUSTOMER-RECEIPT:{reference}",
        source_record=legacy_payment or customer,
        actor=approver,
        lines=[
            JournalLineSpec(
                payment_account.gl_account.system_key,
                debit=snapshot.native_amount,
                customer=customer,
                description=f"Customer receipt {reference}",
            ),
            *credit_lines,
        ],
    )
    journal = post_journal(journal, actor=approver)
    try:
        event = create_draft_event(
            customer=customer,
            source_invoice=pairs[0][0] if len(pairs) == 1 else None,
            kind=ReceivableEvent.KIND_CASH_RECEIPT,
            event_date=receipt_date,
            effective_date=receipt_date,
            native_amount=snapshot.native_amount,
            currency=snapshot.currency,
            rate_to_cad=snapshot.rate_to_cad,
            rate_to_bdt=snapshot.rate_to_bdt,
            idempotency_key=f"CORE:CUSTOMER-RECEIPT:{reference}",
            external_reference=str(reference).strip(),
            evidence_reference=(evidence_reference or str(reference)).strip(),
            legacy_invoice_payment=legacy_payment,
            accounting_entry=legacy_accounting_entry,
            financial_journal=journal,
            actor=approver,
        )
    except DuplicateLedgerWrite as exc:
        raise ReceivableAccountingError(str(exc)) from exc
    event = post_event(event, actor=approver)
    allocations = []
    for invoice, applied in pairs:
        allocation = create_draft_allocation(
            event=event,
            invoice=invoice,
            signed_amount=applied,
            allocation_date=receipt_date,
            idempotency_key=f"CORE:CUSTOMER-RECEIPT:{reference}:INVOICE:{invoice.pk}",
            reason=f"Receipt {reference}",
            actor=approver,
        )
        allocations.append(post_allocation(allocation, actor=approver))
        _sync_payment_status(invoice)
    return {
        "event": event,
        "journal": journal,
        "allocations": allocations,
        "allocated": allocated,
        "customer_credit": unallocated,
        "payment_method": payment_method,
    }


@transaction.atomic
def apply_customer_credit_note(
    *, invoice, amount, credit_date, reference, evidence_reference, actor, rate_to_cad=None, rate_to_bdt=None
):
    approver = _actor(actor)
    locked = Invoice.objects.select_for_update().select_related("customer", "financial_state").get(pk=invoice.pk)
    outstanding = invoice_outstanding(locked)
    amount = money(amount)
    if amount <= 0 or amount > outstanding:
        raise ReceivableAccountingError("Credit note amount must be positive and cannot exceed invoice outstanding.")
    if not evidence_reference:
        raise ReceivableAccountingError("Credit notes require an evidence reference.")
    snapshot = resolve_currency_snapshot(
        native_amount=amount,
        currency=locked.currency,
        transaction_date=credit_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    journal = create_draft_journal(
        journal_date=credit_date,
        reference=f"FIN-CN-{reference}",
        description=f"Credit note {reference} for invoice {locked.invoice_number}",
        side=locked.invoice_region or ("BD" if locked.invoice_market == "bangladesh" else "CA"),
        snapshot=snapshot,
        source_key=f"CREDIT-NOTE:{reference}",
        source_record=locked,
        actor=approver,
        lines=[
            JournalLineSpec(_invoice_revenue_key(locked), debit=amount, customer=locked.customer),
            JournalLineSpec("ACCOUNTS_RECEIVABLE", credit=amount, customer=locked.customer),
        ],
    )
    journal = post_journal(journal, actor=approver)
    event = post_event(
        create_draft_event(
            customer=locked.customer,
            source_invoice=locked,
            kind=ReceivableEvent.KIND_CREDIT_NOTE,
            event_date=credit_date,
            effective_date=credit_date,
            native_amount=amount,
            currency=snapshot.currency,
            rate_to_cad=snapshot.rate_to_cad,
            rate_to_bdt=snapshot.rate_to_bdt,
            idempotency_key=f"CORE:CREDIT-NOTE:{reference}",
            external_reference=reference,
            evidence_reference=evidence_reference,
            financial_journal=journal,
            actor=approver,
        ),
        actor=approver,
    )
    allocation = post_allocation(
        create_draft_allocation(
            event=event,
            invoice=locked,
            signed_amount=amount,
            allocation_date=credit_date,
            idempotency_key=f"CORE:CREDIT-NOTE:{reference}:INVOICE:{locked.pk}",
            reason=evidence_reference,
            actor=approver,
        ),
        actor=approver,
    )
    _sync_payment_status(locked)
    return event, allocation, journal


@transaction.atomic
def record_customer_refund(
    *, customer, amount, currency, refund_date, payment_account, reference, evidence_reference, actor,
    invoice=None, rate_to_cad=None, rate_to_bdt=None
):
    approver = _actor(actor)
    if not evidence_reference:
        raise ReceivableAccountingError("Refunds require an evidence reference.")
    amount = money(amount)
    account_key = "CUSTOMER_DEPOSITS"
    locked_invoice = None
    if invoice:
        locked_invoice = Invoice.objects.select_for_update().get(pk=invoice.pk, customer=customer)
        if amount > money(locked_invoice.financial_state.issued_native_amount - invoice_outstanding(locked_invoice)):
            raise ReceivableAccountingError("Refund cannot exceed the amount settled on the invoice.")
        account_key = "ACCOUNTS_RECEIVABLE"
    snapshot = resolve_currency_snapshot(
        native_amount=amount,
        currency=currency,
        transaction_date=refund_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=locked_invoice or customer,
        actor=approver,
    )
    if payment_account.currency != snapshot.currency:
        raise ReceivableAccountingError("Refund currency must match the cash/bank account currency.")
    journal = post_journal(
        create_draft_journal(
            journal_date=refund_date,
            reference=f"FIN-REF-{reference}",
            description=f"Customer refund {reference}",
            side=payment_account.side,
            snapshot=snapshot,
            source_key=f"CUSTOMER-REFUND:{reference}",
            source_record=locked_invoice or customer,
            actor=approver,
            lines=[
                JournalLineSpec(account_key, debit=amount, customer=customer),
                JournalLineSpec(payment_account.gl_account.system_key, credit=amount, customer=customer),
            ],
        ),
        actor=approver,
    )
    event = post_event(
        create_draft_event(
            customer=customer,
            source_invoice=locked_invoice,
            kind=ReceivableEvent.KIND_REFUND,
            event_date=refund_date,
            effective_date=refund_date,
            native_amount=amount,
            currency=snapshot.currency,
            rate_to_cad=snapshot.rate_to_cad,
            rate_to_bdt=snapshot.rate_to_bdt,
            idempotency_key=f"CORE:CUSTOMER-REFUND:{reference}",
            external_reference=reference,
            evidence_reference=evidence_reference,
            financial_journal=journal,
            actor=approver,
        ),
        actor=approver,
    )
    allocation = None
    if locked_invoice:
        allocation = post_allocation(
            create_draft_allocation(
                event=event,
                invoice=locked_invoice,
                signed_amount=-amount,
                allocation_date=refund_date,
                idempotency_key=f"CORE:CUSTOMER-REFUND:{reference}:INVOICE:{locked_invoice.pk}",
                reason=evidence_reference,
                actor=approver,
            ),
            actor=approver,
        )
        _sync_payment_status(locked_invoice)
    return event, allocation, journal
