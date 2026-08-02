import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from django.conf import settings

from crm.models import (
    AccountingEntry,
    AccountingEntryAudit,
    AccountingMonthClose,
    AccountingMonthLock,
    CRMAuditLog,
    Invoice,
    InvoicePayment,
)
from crm.services.invoice_state import write_payment_projection


MONEY = Decimal("0.01")


class PaymentWriteError(ValueError):
    pass


class UnsupportedReceivableOperation(PaymentWriteError):
    pass


@dataclass(frozen=True)
class RecordedPayment:
    invoice: Invoice
    payment: InvoicePayment
    accounting_entry: AccountingEntry


def _decimal(value) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value) -> Decimal:
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _actor_or_none(actor):
    return actor if actor and getattr(actor, "is_authenticated", False) else None


def invoice_payment_side(invoice: Invoice) -> str:
    region = (getattr(invoice, "invoice_region", "") or "").upper().strip()
    if region in {"CA", "BD"}:
        return region
    currency = (getattr(invoice, "currency", "") or "").upper().strip()
    return "BD" if currency == "BDT" else "CA"


def is_accounting_period_closed(payment_date, side: str) -> bool:
    if not payment_date:
        return False

    side = (side or "").upper().strip()
    lock_filter = {
        "year": payment_date.year,
        "month": payment_date.month,
        "is_closed": True,
    }
    if AccountingMonthClose.objects.filter(
        **lock_filter,
        side__in=[side, "ALL"],
    ).exists():
        return True

    lock_fields = {field.name for field in AccountingMonthLock._meta.fields}
    if "side" in lock_fields and side:
        lock_filter["side"] = side
    return AccountingMonthLock.objects.filter(**lock_filter).exists()


def reconciliation_difference(invoice: Invoice) -> Decimal:
    recorded = invoice.payments.aggregate(total_paid=Sum("amount"))["total_paid"] or Decimal("0")
    return _money(_decimal(invoice.paid_amount) - recorded)


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


def _audit_accounting_entry(entry: AccountingEntry, actor, note: str = "") -> None:
    try:
        AccountingEntryAudit.objects.create(
            entry=entry,
            action="CREATE",
            changed_by=_actor_or_none(actor),
            after_data=_entry_snapshot(entry),
            note=note or "",
        )
    except Exception:
        pass


def _payment_delete_audit_payload(payment: InvoicePayment, invoice: Invoice, reason: str, actor) -> dict:
    return {
        "deleted_payment_id": payment.pk,
        "invoice_id": invoice.pk,
        "invoice_number": invoice.invoice_number,
        "original_amount": str(_money(payment.amount)),
        "currency": payment.currency,
        "payment_date": str(payment.payment_date) if payment.payment_date else "",
        "payment_method": payment.get_payment_method_display(),
        "deleted_by": getattr(actor, "username", "") if _actor_or_none(actor) else "",
        "deleted_time": timezone.now().isoformat(),
        "deletion_reason": reason,
        "accounting_entry_id": payment.accounting_entry_id,
    }


def _audit_payment_delete(payment: InvoicePayment, invoice: Invoice, actor, reason: str) -> None:
    payload = _payment_delete_audit_payload(payment, invoice, reason, actor)
    CRMAuditLog.objects.create(
        actor=_actor_or_none(actor),
        module="invoice_payment",
        record_id=str(payment.pk),
        record_label=f"{invoice.invoice_number} payment {payment.pk}",
        action_type=CRMAuditLog.ACTION_DELETED,
        field_name="payment",
        previous_value=json.dumps(payload, sort_keys=True),
        new_value=reason,
        target_url=reverse("invoice_view", args=[invoice.pk]),
    )


def _validate_new_payment(invoice: Invoice, payment: InvoicePayment) -> None:
    amount = _decimal(payment.amount)
    if amount <= 0:
        raise PaymentWriteError("Enter a payment amount greater than zero.")
    if invoice.status == "cancelled" or invoice.is_archived:
        raise PaymentWriteError("Payments cannot be recorded against a cancelled or archived invoice.")

    invoice_currency = (invoice.currency or "").upper().strip()
    payment_currency = (payment.currency or "").upper().strip()
    if payment_currency != invoice_currency:
        raise PaymentWriteError("Payment currency must match the invoice currency.")

    expected_side = invoice_payment_side(invoice)
    payment_side = (payment.side or "").upper().strip()
    if payment_side != expected_side:
        raise PaymentWriteError(f"Payment side must be {expected_side} for this invoice.")

    outstanding = _decimal(invoice.total_amount) - _decimal(invoice.paid_amount)
    if outstanding <= 0:
        raise PaymentWriteError("This invoice has no outstanding balance.")
    if amount > outstanding:
        raise PaymentWriteError(
            "Payment exceeds the outstanding invoice balance. Unapplied customer credits require Phase 3B approval."
        )
    if is_accounting_period_closed(payment.payment_date, payment_side):
        raise PaymentWriteError(
            f"{payment_side} accounting is closed for {payment.payment_date:%Y-%m}. Open the month before recording this payment."
        )


@transaction.atomic
def record_invoice_payment(invoice: Invoice, payment: InvoicePayment, *, actor=None) -> RecordedPayment:
    locked = Invoice.objects.select_for_update().select_related("customer", "order").get(pk=invoice.pk)
    payment.invoice = locked
    payment.side = (payment.side or invoice_payment_side(locked)).upper().strip()
    payment.created_by = _actor_or_none(actor)
    if not payment.production_order_id and locked.order_id:
        payment.production_order = locked.order

    _validate_new_payment(locked, payment)
    payment.save()

    entry = AccountingEntry.objects.create(
        date=payment.payment_date,
        side=payment.side,
        direction=AccountingEntry.DIR_IN,
        status="PAID",
        main_type="INCOME",
        sub_type="Invoice payment received",
        customer=locked.customer,
        production_order=payment.production_order,
        currency=payment.currency,
        amount_original=payment.amount,
        rate_to_cad=payment.rate_to_cad,
        rate_to_bdt=payment.rate_to_bdt,
        description=f"Payment received for invoice {locked.invoice_number}",
        internal_note=payment.notes or "",
        created_by=_actor_or_none(actor),
    )
    _audit_accounting_entry(entry, actor, note=f"Invoice payment {locked.invoice_number}")

    payment.accounting_entry = entry
    payment.save(update_fields=["accounting_entry"])
    if getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False):
        from crm.services.receivable_accounting import record_customer_receipt, resolve_legacy_payment_account

        payment_account = resolve_legacy_payment_account(
            side=payment.side,
            currency=payment.currency,
            payment_method=payment.payment_method,
        )
        record_customer_receipt(
            customer=locked.customer,
            amount=payment.amount,
            currency=payment.currency,
            receipt_date=payment.payment_date,
            payment_account=payment_account,
            reference=f"LEGACY-{payment.pk}",
            actor=actor,
            invoices=(locked,),
            payment_method=payment.payment_method,
            rate_to_cad=payment.rate_to_cad,
            rate_to_bdt=payment.rate_to_bdt,
            evidence_reference=f"Invoice payment {payment.pk}",
            legacy_payment=payment,
            legacy_accounting_entry=entry,
        )
    write_payment_projection(
        locked,
        paid_amount=_decimal(locked.paid_amount) + _decimal(payment.amount),
        actor=actor,
        sync_lifecycle=True,
    )
    return RecordedPayment(invoice=locked, payment=payment, accounting_entry=entry)


@transaction.atomic
def delete_invoice_payment(invoice: Invoice, payment: InvoicePayment, *, actor=None, reason: str) -> Invoice:
    reason = (reason or "").strip()
    if not reason:
        raise PaymentWriteError("A deletion reason is required before removing a payment.")

    locked_invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    locked_payment = (
        InvoicePayment.objects.select_for_update()
        .select_related("accounting_entry", "production_order", "created_by")
        .get(pk=payment.pk, invoice=locked_invoice)
    )
    if hasattr(locked_payment, "receivable_event") and locked_payment.receivable_event.financial_journal_id:
        raise UnsupportedReceivableOperation(
            "Financial Core payments cannot be deleted; create a traced refund or reversal."
        )
    accounting_entry = locked_payment.accounting_entry
    locked_date = accounting_entry.date if accounting_entry else locked_payment.payment_date
    locked_side = accounting_entry.side if accounting_entry else locked_payment.side
    if is_accounting_period_closed(locked_date, locked_side):
        raise PaymentWriteError("This payment is in a locked accounting period. Create a reversal entry instead.")
    if accounting_entry and accounting_entry.invoice_payments.exclude(pk=locked_payment.pk).exists():
        raise PaymentWriteError(
            "This payment shares an accounting entry with another payment. Review the accounting entry before deleting."
        )

    previous_paid = _decimal(locked_invoice.paid_amount)
    payment_amount = _decimal(locked_payment.amount)
    if accounting_entry:
        AccountingEntryAudit.objects.create(
            entry=accounting_entry,
            action="DELETE",
            changed_by=_actor_or_none(actor),
            before_data=_entry_snapshot(accounting_entry),
            note=f"Deleted invoice payment {locked_payment.pk}",
        )
    _audit_payment_delete(locked_payment, locked_invoice, actor, reason)

    locked_payment.delete()
    if accounting_entry:
        accounting_entry.delete()
    write_payment_projection(
        locked_invoice,
        paid_amount=max(previous_paid - payment_amount, Decimal("0")),
        actor=actor,
        sync_lifecycle=False,
    )
    return locked_invoice


def record_refund(*args, **kwargs):
    raise UnsupportedReceivableOperation(
        "Refund posting is not available in Phase 3A because no approved receivables ledger exists."
    )


def apply_credit_note(*args, **kwargs):
    raise UnsupportedReceivableOperation(
        "Credit-note posting is not available in Phase 3A because no approved receivables ledger exists."
    )
