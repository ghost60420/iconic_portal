from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMAuditLog, Invoice
from crm.services.opportunity_payment_stage import sync_opportunity_stage_from_invoice
from crm.services.order_lifecycle import create_lifecycle_from_invoice


DOCUMENT_DRAFT = "DRAFT"
DOCUMENT_ISSUED = "ISSUED"
DOCUMENT_VOIDED = "VOIDED"

APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"

SETTLEMENT_UNPAID = "UNPAID"
SETTLEMENT_PARTIAL = "PARTIAL"
SETTLEMENT_SETTLED = "SETTLED"

MONEY = Decimal("0.01")


class InvoiceStateError(ValueError):
    pass


@dataclass(frozen=True)
class ProtectedInvoiceState:
    status: str
    paid_amount: Decimal
    invoice_status: str
    approved_at: object
    approved_by_id: int | None


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


def canonical_document_status(invoice: Invoice) -> str:
    if invoice.status == "cancelled":
        return DOCUMENT_VOIDED
    if invoice.status == "draft":
        return DOCUMENT_DRAFT
    return DOCUMENT_ISSUED


def canonical_approval_status(invoice: Invoice) -> str:
    if invoice.invoice_status == "APPROVED" and invoice.approved_at and invoice.approved_by_id:
        return APPROVAL_APPROVED
    return APPROVAL_PENDING


def canonical_settlement_status(invoice: Invoice) -> str:
    total = _decimal(invoice.total_amount)
    paid = _decimal(invoice.paid_amount)
    if paid <= 0:
        return SETTLEMENT_UNPAID
    if total > 0 and paid >= total:
        return SETTLEMENT_SETTLED
    return SETTLEMENT_PARTIAL


def capture_protected_state(invoice: Invoice) -> ProtectedInvoiceState:
    return ProtectedInvoiceState(
        status=invoice.status,
        paid_amount=_money(invoice.paid_amount),
        invoice_status=invoice.invoice_status,
        approved_at=invoice.approved_at,
        approved_by_id=invoice.approved_by_id,
    )


def compatibility_status(*, current_status: str, total_amount, paid_amount) -> str:
    """Preserve the established Invoice.status projection without changing its formula."""
    total = _decimal(total_amount)
    paid = _decimal(paid_amount)
    if paid <= 0:
        if current_status not in {"draft", "sent", "cancelled"}:
            return "draft"
        return current_status
    if total > 0 and paid >= total:
        return "paid"
    if total > 0:
        return "partial"
    return current_status


def payment_projection_status(*, current_status: str, total_amount, paid_amount) -> str:
    """Match the established payment add/delete projection exactly."""
    total = _decimal(total_amount)
    paid = _decimal(paid_amount)
    if paid <= 0:
        return "draft" if current_status == "draft" else "sent"
    if total > 0 and paid >= total:
        return "paid"
    if total > 0:
        return "partial"
    return current_status


def _audit_status_change(
    invoice: Invoice,
    actor,
    old_status: str,
    new_status: str,
    *,
    action_type: str | None = None,
    audit_value: str | None = None,
) -> None:
    if old_status == new_status and action_type != CRMAuditLog.ACTION_CREATED:
        return
    try:
        CRMAuditLog.objects.create(
            actor=_actor_or_none(actor),
            module="invoice",
            record_id=str(invoice.pk),
            record_label=invoice.invoice_number or f"Invoice {invoice.pk}",
            action_type=action_type or CRMAuditLog.ACTION_STATUS_CHANGED,
            field_name="status",
            previous_value=old_status or "",
            new_value=audit_value if audit_value is not None else (new_status or ""),
            target_url=reverse("invoice_view", args=[invoice.pk]),
        )
    except Exception:
        # An audit storage failure must not leave an invoice transaction half-written.
        pass


def _sync_invoice_dependents(invoice: Invoice, actor, *, lifecycle: bool) -> None:
    if lifecycle:
        create_lifecycle_from_invoice(invoice, user=actor)
    sync_opportunity_stage_from_invoice(invoice)


@transaction.atomic
def create_draft_invoice(invoice: Invoice, *, actor=None) -> Invoice:
    if invoice.pk:
        raise InvoiceStateError("A new invoice write requires an unsaved Invoice instance.")

    invoice.status = "draft"
    invoice.invoice_status = "DRAFT"
    invoice.approved_at = None
    invoice.approved_by = None
    invoice.paid_amount = Decimal("0.00")
    invoice.save()
    _audit_status_change(
        invoice,
        actor,
        "",
        invoice.status,
        action_type=CRMAuditLog.ACTION_CREATED,
    )
    _sync_invoice_dependents(invoice, actor, lifecycle=True)
    return invoice


@transaction.atomic
def save_invoice_details(
    invoice: Invoice,
    *,
    protected_state: ProtectedInvoiceState,
    actor=None,
) -> Invoice:
    if not invoice.pk:
        raise InvoiceStateError("Invoice detail updates require an existing invoice.")

    invoice.status = compatibility_status(
        current_status=protected_state.status,
        total_amount=invoice.total_amount,
        paid_amount=protected_state.paid_amount,
    )
    invoice.paid_amount = protected_state.paid_amount
    invoice.invoice_status = protected_state.invoice_status
    invoice.approved_at = protected_state.approved_at
    invoice.approved_by_id = protected_state.approved_by_id
    invoice.save()
    _audit_status_change(invoice, actor, protected_state.status, invoice.status)
    _sync_invoice_dependents(invoice, actor, lifecycle=True)
    return invoice


@transaction.atomic
def approve_invoice(invoice: Invoice, *, actor=None) -> tuple[Invoice, bool]:
    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    previous_status = locked.status
    if previous_status != "draft":
        return locked, False

    locked.status = "sent"
    locked.invoice_status = "APPROVED"
    locked.approved_at = timezone.now()
    locked.approved_by = _actor_or_none(actor)
    locked.save(
        update_fields=[
            "status",
            "invoice_status",
            "approved_at",
            "approved_by",
            "updated_at",
        ]
    )
    _audit_status_change(locked, actor, previous_status, locked.status)
    _sync_invoice_dependents(locked, actor, lifecycle=True)
    return locked, True


@transaction.atomic
def void_invoice(invoice: Invoice, *, actor=None, reason: str) -> Invoice:
    reason = (reason or "").strip()
    if not reason:
        raise InvoiceStateError("A reason is required to void an invoice.")

    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    previous_status = locked.status
    locked.status = "cancelled"
    locked.is_archived = True
    locked.archived_at = timezone.now()
    locked.archived_by = _actor_or_none(actor)
    locked.notes = (locked.notes or "").strip()
    actor_name = getattr(actor, "get_username", lambda: "")() or "Unknown"
    void_note = f"Voided by {actor_name} on {timezone.now():%Y-%m-%d %H:%M}: {reason}"
    locked.notes = f"{locked.notes}\n\n{void_note}".strip()
    locked.save(
        update_fields=[
            "status",
            "is_archived",
            "archived_at",
            "archived_by",
            "notes",
            "updated_at",
        ]
    )
    _audit_status_change(
        locked,
        actor,
        previous_status,
        locked.status,
        audit_value=f"{locked.status}: {reason}",
    )
    return locked


def write_payment_projection(
    invoice: Invoice,
    *,
    paid_amount,
    actor=None,
    sync_lifecycle: bool,
) -> Invoice:
    previous_status = invoice.status
    invoice.paid_amount = _money(max(_decimal(paid_amount), Decimal("0")))
    invoice.status = payment_projection_status(
        current_status=previous_status,
        total_amount=invoice.total_amount,
        paid_amount=invoice.paid_amount,
    )
    invoice.updated_at = timezone.now()
    invoice.save(update_fields=["paid_amount", "status", "updated_at"])
    _audit_status_change(invoice, actor, previous_status, invoice.status)
    _sync_invoice_dependents(invoice, actor, lifecycle=sync_lifecycle)
    return invoice
