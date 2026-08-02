from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import PayableAllocation, PayableEvent, SupplierBill
from crm.services.financial_currency import money, resolve_currency_snapshot
from crm.services.financial_journal import JournalLineSpec, create_draft_journal, post_journal, reverse_journal


class PayablesLedgerError(ValueError):
    pass


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise PayablesLedgerError("An authenticated actor is required.")
    return actor


def bill_outstanding(bill) -> Decimal:
    if bill.approval_status != SupplierBill.APPROVAL_APPROVED:
        return Decimal("0")
    allocated = (
        PayableAllocation.objects.filter(
            bill=bill,
            state=PayableAllocation.STATE_POSTED,
        ).aggregate(total=Sum("native_amount"))["total"]
        or Decimal("0")
    )
    return max(money(bill.total_amount - allocated), Decimal("0"))


def _update_bill_projection(bill):
    locked = SupplierBill.objects.select_for_update().get(pk=bill.pk)
    allocated = (
        PayableAllocation.objects.filter(
            bill=locked,
            state=PayableAllocation.STATE_POSTED,
        ).aggregate(total=Sum("native_amount"))["total"]
        or Decimal("0")
    )
    paid = max(money(allocated), Decimal("0"))
    remaining = max(money(locked.total_amount - paid), Decimal("0"))
    if remaining == 0:
        status = SupplierBill.PAYMENT_PAID
    elif paid > 0:
        status = SupplierBill.PAYMENT_PARTIAL
    else:
        status = SupplierBill.PAYMENT_UNPAID
    SupplierBill.objects.filter(pk=locked.pk).update(
        paid_amount=paid,
        remaining_amount=remaining,
        payment_status=status,
        modified_at=timezone.now(),
    )
    locked.refresh_from_db()
    return locked


def _post_payable_event(
    *, supplier, kind, event_date, snapshot, reference, source_key, journal, actor,
    source_bill=None, payment_account=None, payment_method="", reverses_event=None,
    evidence_reference="", migration_batch="",
):
    if PayableEvent.objects.filter(source_key=source_key).exists():
        raise PayablesLedgerError(f"Payable event source key '{source_key}' already exists.")
    if kind in (PayableEvent.KIND_PAYMENT, PayableEvent.KIND_REFUND) and not payment_account:
        raise PayablesLedgerError("Supplier cash events require a payment account.")
    if kind in (PayableEvent.KIND_CREDIT, PayableEvent.KIND_REFUND, PayableEvent.KIND_OPENING, PayableEvent.KIND_ADJUSTMENT, PayableEvent.KIND_REVERSAL) and not evidence_reference:
        raise PayablesLedgerError("Supplier credits, refunds, openings, adjustments, and reversals require evidence.")
    now = timezone.now()
    return PayableEvent.objects.create(
        supplier=supplier,
        source_bill=source_bill,
        kind=kind,
        state=PayableEvent.STATE_POSTED,
        event_date=event_date,
        currency=snapshot.currency,
        native_amount=snapshot.native_amount,
        rate_to_cad=snapshot.rate_to_cad,
        rate_to_bdt=snapshot.rate_to_bdt,
        amount_cad=snapshot.amount_cad,
        amount_bdt=snapshot.amount_bdt,
        payment_account=payment_account,
        payment_method=payment_method,
        reference=reference,
        source_key=source_key,
        journal=journal,
        reverses_event=reverses_event,
        evidence_reference=evidence_reference,
        migration_batch=migration_batch,
        created_by=actor,
        modified_by=actor,
        submitted_by=actor,
        submitted_at=now,
        approved_by=actor,
        approved_at=now,
    )


def _post_allocation(*, event, bill, amount, allocation_date, source_key, actor):
    amount = money(amount)
    if amount <= 0:
        raise PayablesLedgerError("Payable allocation amount must be greater than zero.")
    if event.state != PayableEvent.STATE_POSTED:
        raise PayablesLedgerError("Only posted payable events may be allocated.")
    if event.kind == PayableEvent.KIND_BILL:
        raise PayablesLedgerError("Bill principal events cannot be allocated.")
    if bill.supplier_id != event.supplier_id or bill.currency != event.currency:
        raise PayablesLedgerError("Payable allocation supplier and currency must match the bill.")
    event_allocated = (
        PayableAllocation.objects.filter(event=event, state=PayableAllocation.STATE_POSTED).aggregate(
            total=Sum("native_amount")
        )["total"]
        or Decimal("0")
    )
    if money(event_allocated + amount) > event.native_amount:
        raise PayablesLedgerError("Payable allocations cannot exceed the event amount.")
    if amount > bill_outstanding(bill):
        raise PayablesLedgerError("Payable allocation cannot exceed bill outstanding.")
    now = timezone.now()
    return PayableAllocation.objects.create(
        event=event,
        bill=bill,
        state=PayableAllocation.STATE_POSTED,
        allocation_date=allocation_date,
        native_amount=amount,
        source_key=source_key,
        created_by=actor,
        modified_by=actor,
        submitted_by=actor,
        submitted_at=now,
        approved_by=actor,
        approved_at=now,
    )


@transaction.atomic
def approve_supplier_bill(bill, *, actor, rate_to_cad=None, rate_to_bdt=None):
    approver = _actor(actor)
    locked = SupplierBill.objects.select_for_update().select_related(
        "supplier", "expense_account", "production_order", "department"
    ).get(pk=bill.pk)
    if locked.approval_status == SupplierBill.APPROVAL_APPROVED:
        return locked
    if locked.approval_status not in (SupplierBill.APPROVAL_DRAFT, SupplierBill.APPROVAL_PENDING):
        raise PayablesLedgerError("Only a draft or pending supplier bill can be approved.")
    locked.full_clean()
    snapshot = resolve_currency_snapshot(
        native_amount=locked.total_amount,
        currency=locked.currency,
        transaction_date=locked.bill_date,
        rate_to_cad=rate_to_cad or locked.rate_to_cad,
        rate_to_bdt=rate_to_bdt or locked.rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    journal = post_journal(
        create_draft_journal(
            journal_date=locked.bill_date,
            reference=f"FIN-BILL-{locked.supplier.code}-{locked.bill_number}",
            description=f"Supplier bill {locked.bill_number} from {locked.supplier.name}",
            side=locked.side,
            snapshot=snapshot,
            source_key=f"SUPPLIER-BILL:{locked.pk}",
            source_record=locked,
            actor=approver,
            lines=[
                JournalLineSpec(
                    locked.expense_account.system_key,
                    debit=snapshot.native_amount,
                    supplier=locked.supplier,
                    production_order=locked.production_order,
                    department=locked.department,
                ),
                JournalLineSpec(
                    "ACCOUNTS_PAYABLE",
                    credit=snapshot.native_amount,
                    supplier=locked.supplier,
                    production_order=locked.production_order,
                    department=locked.department,
                ),
            ],
        ),
        actor=approver,
    )
    event = _post_payable_event(
        supplier=locked.supplier,
        source_bill=locked,
        kind=PayableEvent.KIND_BILL,
        event_date=locked.bill_date,
        snapshot=snapshot,
        reference=locked.bill_number,
        source_key=f"CORE:SUPPLIER-BILL:{locked.pk}",
        journal=journal,
        actor=approver,
    )
    now = timezone.now()
    SupplierBill.objects.filter(pk=locked.pk).update(
        approval_status=SupplierBill.APPROVAL_APPROVED,
        rate_to_cad=snapshot.rate_to_cad,
        rate_to_bdt=snapshot.rate_to_bdt,
        amount_cad=snapshot.amount_cad,
        amount_bdt=snapshot.amount_bdt,
        remaining_amount=snapshot.native_amount,
        payable_journal=journal,
        submitted_by=locked.submitted_by or locked.created_by or approver,
        submitted_at=locked.submitted_at or now,
        approved_by=approver,
        approved_at=now,
        modified_by=approver,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked


def _payment_pairs(bills, amount):
    remaining = money(amount)
    pairs = []
    for bill in bills:
        outstanding = bill_outstanding(bill)
        applied = min(remaining, outstanding)
        if applied > 0:
            pairs.append((bill, applied))
            remaining -= applied
        if remaining <= 0:
            break
    return pairs, remaining


@transaction.atomic
def record_supplier_payment(
    *, supplier, amount, currency, payment_date, payment_account, reference, actor, bills=(),
    payment_method="bank", rate_to_cad=None, rate_to_bdt=None, evidence_reference="",
):
    approver = _actor(actor)
    if not reference:
        raise PayablesLedgerError("Supplier payments require a reference.")
    locked_bills = list(
        SupplierBill.objects.select_for_update()
        .filter(pk__in=[bill.pk for bill in bills])
        .order_by("due_date", "id")
    )
    for bill in locked_bills:
        if bill.supplier_id != supplier.pk or bill.currency != currency:
            raise PayablesLedgerError("Every allocated bill must match the payment supplier and currency.")
    snapshot = resolve_currency_snapshot(
        native_amount=amount,
        currency=currency,
        transaction_date=payment_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=supplier,
        actor=approver,
    )
    if payment_account.currency != snapshot.currency:
        raise PayablesLedgerError(
            "Supplier payment currency must match the cash/bank account currency; use an evidenced company transfer separately."
        )
    pairs, advance = _payment_pairs(locked_bills, snapshot.native_amount)
    allocated = snapshot.native_amount - advance
    debit_lines = []
    if allocated:
        debit_lines.append(JournalLineSpec("ACCOUNTS_PAYABLE", debit=allocated, supplier=supplier))
    if advance:
        debit_lines.append(JournalLineSpec("SUPPLIER_ADVANCES", debit=advance, supplier=supplier))
    journal = post_journal(
        create_draft_journal(
            journal_date=payment_date,
            reference=f"FIN-SPAY-{reference}",
            description=f"Supplier payment {reference} to {supplier.name}",
            side=payment_account.side,
            snapshot=snapshot,
            source_key=f"SUPPLIER-PAYMENT:{reference}",
            source_record=supplier,
            actor=approver,
            lines=[
                *debit_lines,
                JournalLineSpec(payment_account.gl_account.system_key, credit=snapshot.native_amount, supplier=supplier),
            ],
        ),
        actor=approver,
    )
    event = _post_payable_event(
        supplier=supplier,
        kind=PayableEvent.KIND_PAYMENT,
        event_date=payment_date,
        snapshot=snapshot,
        reference=reference,
        source_key=f"CORE:SUPPLIER-PAYMENT:{reference}",
        journal=journal,
        payment_account=payment_account,
        payment_method=payment_method,
        evidence_reference=evidence_reference,
        actor=approver,
    )
    allocations = []
    for bill, applied in pairs:
        allocations.append(
            _post_allocation(
                event=event,
                bill=bill,
                amount=applied,
                allocation_date=payment_date,
                source_key=f"CORE:SUPPLIER-PAYMENT:{reference}:BILL:{bill.pk}",
                actor=approver,
            )
        )
        _update_bill_projection(bill)
    return {
        "event": event,
        "journal": journal,
        "allocations": allocations,
        "allocated": allocated,
        "supplier_advance": advance,
    }


@transaction.atomic
def apply_supplier_credit(
    *, bill, amount, credit_date, reference, evidence_reference, actor, rate_to_cad=None, rate_to_bdt=None
):
    approver = _actor(actor)
    locked = SupplierBill.objects.select_for_update().select_related("supplier", "expense_account").get(pk=bill.pk)
    amount = money(amount)
    if amount <= 0 or amount > bill_outstanding(locked):
        raise PayablesLedgerError("Supplier credit must be positive and cannot exceed bill outstanding.")
    if not evidence_reference:
        raise PayablesLedgerError("Supplier credits require evidence.")
    snapshot = resolve_currency_snapshot(
        native_amount=amount,
        currency=locked.currency,
        transaction_date=credit_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    journal = post_journal(
        create_draft_journal(
            journal_date=credit_date,
            reference=f"FIN-SC-{reference}",
            description=f"Supplier credit {reference} for bill {locked.bill_number}",
            side=locked.side,
            snapshot=snapshot,
            source_key=f"SUPPLIER-CREDIT:{reference}",
            source_record=locked,
            actor=approver,
            lines=[
                JournalLineSpec("ACCOUNTS_PAYABLE", debit=amount, supplier=locked.supplier),
                JournalLineSpec(locked.expense_account.system_key, credit=amount, supplier=locked.supplier),
            ],
        ),
        actor=approver,
    )
    event = _post_payable_event(
        supplier=locked.supplier,
        source_bill=locked,
        kind=PayableEvent.KIND_CREDIT,
        event_date=credit_date,
        snapshot=snapshot,
        reference=reference,
        source_key=f"CORE:SUPPLIER-CREDIT:{reference}",
        journal=journal,
        evidence_reference=evidence_reference,
        actor=approver,
    )
    allocation = _post_allocation(
        event=event,
        bill=locked,
        amount=amount,
        allocation_date=credit_date,
        source_key=f"CORE:SUPPLIER-CREDIT:{reference}:BILL:{locked.pk}",
        actor=approver,
    )
    _update_bill_projection(locked)
    return event, allocation, journal


@transaction.atomic
def record_supplier_refund(
    *, supplier, amount, currency, refund_date, payment_account, reference, evidence_reference, actor,
    against_advance=True, rate_to_cad=None, rate_to_bdt=None,
):
    approver = _actor(actor)
    if not evidence_reference:
        raise PayablesLedgerError("Supplier refunds require evidence.")
    snapshot = resolve_currency_snapshot(
        native_amount=amount,
        currency=currency,
        transaction_date=refund_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=supplier,
        actor=approver,
    )
    if payment_account.currency != snapshot.currency:
        raise PayablesLedgerError("Supplier refund currency must match the cash/bank account currency.")
    offset = "SUPPLIER_ADVANCES" if against_advance else "ACCOUNTS_PAYABLE"
    journal = post_journal(
        create_draft_journal(
            journal_date=refund_date,
            reference=f"FIN-SREF-{reference}",
            description=f"Supplier refund {reference} from {supplier.name}",
            side=payment_account.side,
            snapshot=snapshot,
            source_key=f"SUPPLIER-REFUND:{reference}",
            source_record=supplier,
            actor=approver,
            lines=[
                JournalLineSpec(payment_account.gl_account.system_key, debit=snapshot.native_amount, supplier=supplier),
                JournalLineSpec(offset, credit=snapshot.native_amount, supplier=supplier),
            ],
        ),
        actor=approver,
    )
    event = _post_payable_event(
        supplier=supplier,
        kind=PayableEvent.KIND_REFUND,
        event_date=refund_date,
        snapshot=snapshot,
        reference=reference,
        source_key=f"CORE:SUPPLIER-REFUND:{reference}",
        journal=journal,
        payment_account=payment_account,
        evidence_reference=evidence_reference,
        actor=approver,
    )
    return event, journal


@transaction.atomic
def reverse_supplier_bill(bill, *, actor, reason, reversal_date=None):
    approver = _actor(actor)
    locked = SupplierBill.objects.select_for_update().select_related("payable_journal").get(pk=bill.pk)
    if locked.approval_status != SupplierBill.APPROVAL_APPROVED:
        raise PayablesLedgerError("Only an approved supplier bill can be reversed.")
    if locked.remaining_amount != locked.total_amount:
        raise PayablesLedgerError("Allocated supplier bills require payment/credit reversals first.")
    original_event = PayableEvent.objects.get(source_bill=locked, kind=PayableEvent.KIND_BILL)
    reversal_journal = reverse_journal(
        locked.payable_journal,
        actor=approver,
        reason=reason,
        reversal_date=reversal_date or timezone.localdate(),
    )
    snapshot = resolve_currency_snapshot(
        native_amount=locked.total_amount,
        currency=locked.currency,
        transaction_date=locked.bill_date,
        rate_to_cad=locked.rate_to_cad,
        rate_to_bdt=locked.rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    reversal_event = _post_payable_event(
        supplier=locked.supplier,
        source_bill=locked,
        kind=PayableEvent.KIND_REVERSAL,
        event_date=reversal_journal.journal_date,
        snapshot=snapshot,
        reference=reversal_journal.reference,
        source_key=f"CORE:SUPPLIER-BILL:{locked.pk}:REVERSAL",
        journal=reversal_journal,
        reverses_event=original_event,
        evidence_reference=reason,
        actor=approver,
    )
    PayableEvent.objects.filter(pk=original_event.pk).update(state=PayableEvent.STATE_REVERSED)
    SupplierBill.objects.filter(pk=locked.pk).update(
        approval_status=SupplierBill.APPROVAL_REVERSED,
        payment_status=SupplierBill.PAYMENT_UNPAID,
        remaining_amount=Decimal("0"),
        change_reason=reason,
        modified_by=approver,
        modified_at=timezone.now(),
    )
    locked.refresh_from_db()
    return locked, reversal_event, reversal_journal
