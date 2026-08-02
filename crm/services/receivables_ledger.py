from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import AccountingEntry, Invoice, JournalEntry, ReceivableAllocation, ReceivableEvent
from crm.services.costing_currency import CurrencyConversionError, convert_currency


MONEY = Decimal("0.01")
RATE = Decimal("0.000001")


class ReceivablesLedgerError(ValueError):
    pass


class DuplicateLedgerWrite(ReceivablesLedgerError):
    pass


class LedgerStateError(ReceivablesLedgerError):
    pass


def _decimal(value) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value) -> Decimal:
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ReceivablesLedgerError("An authenticated approver is required for ledger posting.")
    return actor


def _currency_snapshot(native_amount, currency, *, rate_to_cad=0, rate_to_bdt=0) -> dict:
    amount = _money(native_amount)
    currency = (currency or "").upper().strip()
    cad_rate = _decimal(rate_to_cad)
    bdt_rate = _decimal(rate_to_bdt)

    if currency == "CAD":
        cad_rate = Decimal("1")
    elif currency == "BDT":
        bdt_rate = Decimal("1")
    cad_rate = cad_rate.quantize(RATE, rounding=ROUND_HALF_UP)
    bdt_rate = bdt_rate.quantize(RATE, rounding=ROUND_HALF_UP)

    try:
        amount_cad = convert_currency(
            amount,
            currency,
            "CAD",
            stored_rate_to_cad=cad_rate,
            stored_rate_to_bdt=bdt_rate,
        )
        amount_bdt = convert_currency(
            amount,
            currency,
            "BDT",
            stored_rate_to_cad=cad_rate,
            stored_rate_to_bdt=bdt_rate,
        )
    except CurrencyConversionError as exc:
        raise ReceivablesLedgerError(str(exc)) from exc

    return {
        "native_amount": amount,
        "currency": currency,
        "rate_to_cad": cad_rate,
        "rate_to_bdt": bdt_rate,
        "amount_cad": amount_cad,
        "amount_bdt": amount_bdt,
    }


def _raise_duplicate(kind: str, key: str) -> None:
    raise DuplicateLedgerWrite(f"A {kind} with idempotency key '{key}' already exists.")


@transaction.atomic
def create_draft_event(
    *,
    customer,
    kind,
    event_date,
    effective_date,
    native_amount,
    currency,
    idempotency_key,
    rate_to_cad=0,
    rate_to_bdt=0,
    external_reference="",
    reason="",
    evidence_reference="",
    migration_batch="",
    source_invoice=None,
    legacy_invoice_payment=None,
    accounting_entry=None,
    financial_journal=None,
    reverses_event=None,
    actor=None,
) -> ReceivableEvent:
    key = (idempotency_key or "").strip()
    if not key:
        raise ReceivablesLedgerError("A receivable event idempotency key is required.")
    if ReceivableEvent.objects.filter(idempotency_key=key).exists():
        _raise_duplicate("receivable event", key)

    snapshot = _currency_snapshot(
        native_amount,
        currency,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
    )
    try:
        return ReceivableEvent.objects.create(
            customer=customer,
            source_invoice=source_invoice,
            kind=kind,
            state=ReceivableEvent.STATE_DRAFT,
            event_date=event_date,
            effective_date=effective_date,
            idempotency_key=key,
            external_reference=(external_reference or "").strip(),
            reason=(reason or "").strip(),
            evidence_reference=(evidence_reference or "").strip(),
            migration_batch=(migration_batch or "").strip(),
            legacy_invoice_payment=legacy_invoice_payment,
            accounting_entry=accounting_entry,
            financial_journal=financial_journal,
            reverses_event=reverses_event,
            created_by=actor if actor and getattr(actor, "is_authenticated", False) else None,
            **snapshot,
        )
    except (IntegrityError, ValidationError) as exc:
        if ReceivableEvent.objects.filter(idempotency_key=key).exists():
            _raise_duplicate("receivable event", key)
        raise ReceivablesLedgerError(str(exc)) from exc


def _validate_accounting_link(event: ReceivableEvent) -> None:
    cash_kinds = {ReceivableEvent.KIND_CASH_RECEIPT, ReceivableEvent.KIND_REFUND}
    if event.kind in cash_kinds and not event.external_reference.strip():
        raise ReceivablesLedgerError("Cash receipts and refunds require an external reference.")
    if event.kind in cash_kinds and not (event.accounting_entry_id or event.financial_journal_id):
        raise ReceivablesLedgerError("Cash receipts and refunds require a linked accounting entry or financial journal.")
    if not event.accounting_entry_id:
        return

    entry = event.accounting_entry
    expected_direction = {
        ReceivableEvent.KIND_CASH_RECEIPT: AccountingEntry.DIR_IN,
        ReceivableEvent.KIND_REFUND: AccountingEntry.DIR_OUT,
    }.get(event.kind)
    if expected_direction and entry.direction != expected_direction:
        raise ReceivablesLedgerError(
            f"{event.get_kind_display()} requires an {expected_direction} accounting entry."
        )
    if entry.customer_id != event.customer_id:
        raise ReceivablesLedgerError("Accounting entry customer must match the receivable event customer.")
    if (entry.currency or "").upper().strip() != event.currency:
        raise ReceivablesLedgerError("Accounting entry currency must match the receivable event currency.")
    if _money(entry.amount_original) != _money(event.native_amount):
        raise ReceivablesLedgerError("Accounting entry amount must match the receivable event amount.")
    if entry.date != event.event_date:
        raise ReceivablesLedgerError("Accounting entry date must match the receivable event date.")
    for field in ("rate_to_cad", "rate_to_bdt"):
        if _decimal(getattr(entry, field)) != _decimal(getattr(event, field)):
            raise ReceivablesLedgerError("Accounting entry exchange rates must match the receivable event snapshot.")
    for field in ("amount_cad", "amount_bdt"):
        if _money(getattr(entry, field)) != _money(getattr(event, field)):
            raise ReceivablesLedgerError("Accounting entry converted amounts must match the receivable event snapshot.")


def _validate_financial_journal(event: ReceivableEvent) -> None:
    if not event.financial_journal_id:
        return
    journal = event.financial_journal
    if journal.state != JournalEntry.STATE_POSTED:
        raise ReceivablesLedgerError("Receivable events require a posted financial journal.")
    if journal.currency != event.currency or journal.journal_date != event.event_date:
        raise ReceivablesLedgerError("Financial journal currency and date must match the receivable event.")
    if _decimal(journal.rate_to_cad) != _decimal(event.rate_to_cad):
        raise ReceivablesLedgerError("Financial journal CAD rate must match the receivable event snapshot.")
    if _decimal(journal.rate_to_bdt) != _decimal(event.rate_to_bdt):
        raise ReceivablesLedgerError("Financial journal BDT rate must match the receivable event snapshot.")
    totals = journal.lines.aggregate(debit=Sum("native_debit"), credit=Sum("native_credit"))
    if _money(totals["debit"]) != _money(event.native_amount) or _money(totals["credit"]) != _money(event.native_amount):
        raise ReceivablesLedgerError("Financial journal amount must match the receivable event amount.")


def _validate_legacy_payment_link(event: ReceivableEvent) -> None:
    if not event.legacy_invoice_payment_id:
        return
    if event.kind not in {ReceivableEvent.KIND_CASH_RECEIPT, ReceivableEvent.KIND_OPENING_RECEIPT}:
        raise ReceivablesLedgerError("Only cash or opening receipts may link to a legacy invoice payment.")

    payment = event.legacy_invoice_payment
    if payment.invoice.customer_id != event.customer_id:
        raise ReceivablesLedgerError("Legacy payment customer must match the receivable event customer.")
    if (payment.currency or "").upper().strip() != event.currency:
        raise ReceivablesLedgerError("Legacy payment currency must match the receivable event currency.")
    if _money(payment.amount) != _money(event.native_amount):
        raise ReceivablesLedgerError("Legacy payment amount must match the receivable event amount.")
    if payment.payment_date != event.event_date:
        raise ReceivablesLedgerError("Legacy payment date must match the receivable event date.")
    if payment.accounting_entry_id and payment.accounting_entry_id != event.accounting_entry_id:
        raise ReceivablesLedgerError("Legacy payment and receivable event must link to the same accounting entry.")
    if event.source_invoice_id and event.source_invoice_id != payment.invoice_id:
        raise ReceivablesLedgerError("Legacy payment and receivable event must link to the same invoice.")


def _validate_source_invoice(event: ReceivableEvent) -> None:
    invoice = event.source_invoice
    if event.kind == ReceivableEvent.KIND_INVOICE_ISSUED and not invoice:
        raise ReceivablesLedgerError("Invoice-issued events require a source invoice.")
    if not invoice:
        return
    if invoice.customer_id != event.customer_id:
        raise ReceivablesLedgerError("Source invoice customer must match the receivable event customer.")
    if (invoice.currency or "").upper().strip() != event.currency:
        raise ReceivablesLedgerError("Source invoice currency must match the receivable event currency.")
    if event.kind != ReceivableEvent.KIND_INVOICE_ISSUED:
        return
    if _money(invoice.total_amount) != _money(event.native_amount):
        raise ReceivablesLedgerError("Invoice-issued event amount must match the source invoice total.")
    if invoice.effective_invoice_date != event.effective_date:
        raise ReceivablesLedgerError("Invoice-issued event date must match the effective invoice date.")
    if event.accounting_entry_id or event.legacy_invoice_payment_id or event.reverses_event_id:
        raise ReceivablesLedgerError("Invoice-issued events cannot masquerade as payments or reversals.")


def _validate_evidence(event: ReceivableEvent) -> None:
    evidence_kinds = {
        ReceivableEvent.KIND_CREDIT_NOTE,
        ReceivableEvent.KIND_REFUND,
        ReceivableEvent.KIND_OPENING_RECEIPT,
        ReceivableEvent.KIND_ADJUSTMENT,
        ReceivableEvent.KIND_REVERSAL,
    }
    if event.kind in evidence_kinds and not event.evidence_reference.strip():
        raise ReceivablesLedgerError(f"{event.get_kind_display()} requires an evidence reference.")
    if event.kind == ReceivableEvent.KIND_OPENING_RECEIPT and not event.migration_batch.strip():
        raise ReceivablesLedgerError("Opening receipts require a migration batch reference.")
    if event.kind == ReceivableEvent.KIND_REVERSAL:
        original = event.reverses_event
        if original.state != ReceivableEvent.STATE_POSTED:
            raise ReceivablesLedgerError("Only a posted receivable event can be reversed.")
        if original.customer_id != event.customer_id or original.currency != event.currency:
            raise ReceivablesLedgerError("A reversal must match the original event customer and currency.")
        if _money(original.native_amount) != _money(event.native_amount):
            raise ReceivablesLedgerError("A reversal amount must exactly match the original event amount.")


@transaction.atomic
def post_event(event: ReceivableEvent, *, actor) -> ReceivableEvent:
    approver = _actor(actor)
    locked = (
        ReceivableEvent.objects.select_for_update()
        .select_related(
            "accounting_entry",
            "financial_journal",
            "legacy_invoice_payment__invoice",
            "reverses_event",
            "source_invoice",
        )
        .get(pk=event.pk)
    )
    if locked.state != ReceivableEvent.STATE_DRAFT:
        raise LedgerStateError("Only a draft receivable event can be posted.")

    _validate_accounting_link(locked)
    _validate_financial_journal(locked)
    _validate_legacy_payment_link(locked)
    _validate_source_invoice(locked)
    _validate_evidence(locked)
    posted_at = timezone.now()
    locked.state = ReceivableEvent.STATE_POSTED
    locked.posted_at = posted_at
    locked.approved_at = posted_at
    locked.approved_by = approver
    locked.save(update_fields=["state", "posted_at", "approved_at", "approved_by", "updated_at"])
    return locked


@transaction.atomic
def create_draft_allocation(
    *,
    event,
    invoice,
    signed_amount,
    allocation_date,
    idempotency_key,
    reason="",
    reverses_allocation=None,
    actor=None,
) -> ReceivableAllocation:
    key = (idempotency_key or "").strip()
    if not key:
        raise ReceivablesLedgerError("A receivable allocation idempotency key is required.")
    if ReceivableAllocation.objects.filter(idempotency_key=key).exists():
        _raise_duplicate("receivable allocation", key)

    try:
        return ReceivableAllocation.objects.create(
            event=event,
            invoice=invoice,
            reverses_allocation=reverses_allocation,
            signed_amount=_money(signed_amount),
            currency=(invoice.currency or "").upper().strip(),
            allocation_date=allocation_date,
            idempotency_key=key,
            reason=(reason or "").strip(),
            created_by=actor if actor and getattr(actor, "is_authenticated", False) else None,
        )
    except (IntegrityError, ValidationError) as exc:
        if ReceivableAllocation.objects.filter(idempotency_key=key).exists():
            _raise_duplicate("receivable allocation", key)
        raise ReceivablesLedgerError(str(exc)) from exc


def _validate_allocation_direction(allocation: ReceivableAllocation) -> None:
    event = allocation.event
    amount = _money(allocation.signed_amount)
    positive_kinds = {
        ReceivableEvent.KIND_CASH_RECEIPT,
        ReceivableEvent.KIND_CREDIT_NOTE,
        ReceivableEvent.KIND_OPENING_RECEIPT,
    }
    negative_kinds = {ReceivableEvent.KIND_REFUND, ReceivableEvent.KIND_REVERSAL}
    if event.kind in positive_kinds and amount <= 0:
        raise ReceivablesLedgerError(f"{event.get_kind_display()} allocations must be positive.")
    if event.kind in negative_kinds and amount >= 0:
        raise ReceivablesLedgerError(f"{event.get_kind_display()} allocations must be negative.")
    if event.kind == ReceivableEvent.KIND_ADJUSTMENT and amount == 0:
        raise ReceivablesLedgerError("Adjustment allocations cannot be zero.")

    if event.kind == ReceivableEvent.KIND_REVERSAL:
        original = allocation.reverses_allocation
        if not original:
            raise ReceivablesLedgerError("A reversal allocation must reference the allocation it reverses.")
        if original.state != ReceivableAllocation.STATE_POSTED:
            raise ReceivablesLedgerError("Only a posted allocation can be reversed.")
        if original.event_id != event.reverses_event_id or original.invoice_id != allocation.invoice_id:
            raise ReceivablesLedgerError("A reversal allocation must match the original event and invoice.")
        if amount != -_money(original.signed_amount):
            raise ReceivablesLedgerError("A reversal allocation must exactly offset the original allocation.")


def _validate_allocation_capacity(allocation: ReceivableAllocation) -> None:
    existing = (
        ReceivableAllocation.objects.filter(
            event=allocation.event,
            state=ReceivableAllocation.STATE_POSTED,
        )
        .exclude(pk=allocation.pk)
        .aggregate(total=Sum("signed_amount"))["total"]
        or Decimal("0")
    )
    proposed = _money(existing + allocation.signed_amount)
    event_amount = abs(_money(allocation.event.native_amount))
    if abs(proposed) > event_amount:
        raise ReceivablesLedgerError("Posted allocations cannot exceed the receivable event amount.")


@transaction.atomic
def post_allocation(allocation: ReceivableAllocation, *, actor) -> ReceivableAllocation:
    approver = _actor(actor)
    locked = (
        ReceivableAllocation.objects.select_for_update()
        .select_related("event", "event__reverses_event", "invoice", "reverses_allocation")
        .get(pk=allocation.pk)
    )
    if locked.state != ReceivableAllocation.STATE_DRAFT:
        raise LedgerStateError("Only a draft receivable allocation can be posted.")
    if locked.event.state != ReceivableEvent.STATE_POSTED:
        raise ReceivablesLedgerError("Allocations require a posted receivable event.")
    if locked.event.kind == ReceivableEvent.KIND_INVOICE_ISSUED:
        raise ReceivablesLedgerError("Invoice-issued events establish principal and cannot be allocated.")
    if locked.invoice.customer_id != locked.event.customer_id:
        raise ReceivablesLedgerError("Allocation invoice customer must match the receivable event customer.")
    invoice_currency = (locked.invoice.currency or "").upper().strip()
    if locked.currency != locked.event.currency or locked.currency != invoice_currency:
        raise ReceivablesLedgerError("Event, allocation, and invoice currencies must match.")

    _validate_allocation_direction(locked)
    _validate_allocation_capacity(locked)
    posted_at = timezone.now()
    locked.state = ReceivableAllocation.STATE_POSTED
    locked.posted_at = posted_at
    locked.approved_at = posted_at
    locked.approved_by = approver
    locked.save(update_fields=["state", "posted_at", "approved_at", "approved_by", "updated_at"])
    return locked


def posted_allocation_totals(invoice_ids) -> dict[int, Decimal]:
    rows = (
        ReceivableAllocation.objects.filter(
            invoice_id__in=tuple(invoice_ids),
            state=ReceivableAllocation.STATE_POSTED,
        )
        .values("invoice_id")
        .annotate(total=Sum("signed_amount"))
    )
    return {row["invoice_id"]: _money(row["total"]) for row in rows}
