from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from crm.models import FinancialAdjustmentRequest, FinancialAuditEvent
from crm.services.financial_currency import CurrencySnapshot, money
from crm.services.financial_journal import (
    JournalLineSpec,
    create_draft_journal,
    post_journal,
    reverse_journal,
)
from crm.services.financial_permissions import (
    can_approve_financial_adjustment,
    can_create_financial_adjustment,
)


class FinancialAdjustmentError(ValueError):
    pass


def _audit(record, action, actor, *, reason="", before=None, after=None):
    FinancialAuditEvent.objects.create(
        content_type=ContentType.objects.get_for_model(record, for_concrete_model=False),
        object_id=record.pk,
        action=action,
        reason=reason,
        before_value=before or {},
        after_value=after or {},
        source_reference=record.request_id,
        actor=actor,
    )


def _require_text(value, label):
    value = (value or "").strip()
    if not value:
        raise FinancialAdjustmentError(f"{label} is required.")
    return value


@transaction.atomic
def submit_adjustment_request(*, actor, evidence_documents=(), **values):
    if not can_create_financial_adjustment(actor):
        raise FinancialAdjustmentError("You do not have permission to propose financial adjustments.")
    evidence_documents = tuple(evidence_documents)
    if not evidence_documents:
        raise FinancialAdjustmentError("At least one supporting evidence document is required.")
    values["reason"] = _require_text(values.get("reason"), "Reason")
    if not values.get("before_values") or not values.get("after_values"):
        raise FinancialAdjustmentError("Before and after values are required.")
    now = timezone.now()
    request = FinancialAdjustmentRequest(
        **values,
        state=FinancialAdjustmentRequest.STATE_SUBMITTED,
        created_by=actor,
        modified_by=actor,
        submitted_by=actor,
        submitted_at=now,
    )
    request.full_clean()
    request.save()
    request.evidence_documents.set(evidence_documents)
    _audit(
        request,
        "SUBMITTED",
        actor,
        reason=request.reason,
        after={"state": request.state, "before": request.before_values, "after": request.after_values},
    )
    return request


@transaction.atomic
def decide_adjustment_request(request, *, actor, approve, notes):
    if not can_approve_financial_adjustment(actor):
        raise FinancialAdjustmentError("You do not have permission to approve financial adjustments.")
    notes = _require_text(notes, "Approval notes")
    locked = FinancialAdjustmentRequest.objects.select_for_update().get(pk=request.pk)
    if locked.state != FinancialAdjustmentRequest.STATE_SUBMITTED:
        raise FinancialAdjustmentError("Only submitted adjustment requests may be decided.")
    if locked.created_by_id == actor.pk:
        raise FinancialAdjustmentError("A user may not approve their own high-risk adjustment.")
    if not locked.evidence_documents.exists():
        raise FinancialAdjustmentError("Supporting evidence is required before approval.")
    previous = locked.state
    now = timezone.now()
    locked.state = (
        FinancialAdjustmentRequest.STATE_APPROVED
        if approve
        else FinancialAdjustmentRequest.STATE_REJECTED
    )
    locked.approval_notes = notes
    locked.approved_by = actor if approve else None
    locked.approved_at = now if approve else None
    locked.modified_by = actor
    locked.save(
        update_fields=("state", "approval_notes", "approved_by", "approved_at", "modified_by", "modified_at")
    )
    _audit(
        locked,
        "APPROVED" if approve else "REJECTED",
        actor,
        reason=notes,
        before={"state": previous},
        after={"state": locked.state},
    )
    return locked


@transaction.atomic
def post_approved_adjustment(request, *, actor):
    if not getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False):
        raise FinancialAdjustmentError("Finance posting is disabled.")
    if not can_approve_financial_adjustment(actor):
        raise FinancialAdjustmentError("You do not have permission to post financial adjustments.")
    locked = FinancialAdjustmentRequest.objects.select_for_update().select_related(
        "debit_account", "credit_account", "source_journal"
    ).get(pk=request.pk)
    if locked.state != FinancialAdjustmentRequest.STATE_APPROVED:
        raise FinancialAdjustmentError("Only approved adjustment requests may be posted.")
    if locked.created_by_id == actor.pk:
        raise FinancialAdjustmentError("A user may not post their own high-risk adjustment.")

    if locked.adjustment_type in {
        FinancialAdjustmentRequest.TYPE_RECEIVABLE,
        FinancialAdjustmentRequest.TYPE_PAYABLE,
        FinancialAdjustmentRequest.TYPE_CURRENCY,
    }:
        raise FinancialAdjustmentError(
            "This correction must be posted by its dedicated receivable, payable, or currency service."
        )
    if locked.adjustment_type == FinancialAdjustmentRequest.TYPE_REVERSAL:
        journal = reverse_journal(
            locked.source_journal,
            actor=actor,
            reason=locked.reason,
            reversal_date=locked.journal_date,
        )
    else:
        if locked.adjustment_type == FinancialAdjustmentRequest.TYPE_MANUAL and (
            not locked.debit_account.allow_manual_posting or not locked.credit_account.allow_manual_posting
        ):
            raise FinancialAdjustmentError("The selected control account does not allow manual posting.")
        amount = money(locked.native_amount)
        snapshot = CurrencySnapshot(
            native_amount=amount,
            currency=locked.currency,
            rate_to_cad=locked.rate_to_cad,
            rate_to_bdt=locked.rate_to_bdt,
            amount_cad=money(amount * locked.rate_to_cad),
            amount_bdt=money(amount * locked.rate_to_bdt),
            rate_path=("APPROVED_ADJUSTMENT_SNAPSHOT",),
        )
        journal = create_draft_journal(
            journal_date=locked.journal_date,
            reference=locked.request_id,
            description=locked.reason,
            side=locked.side,
            snapshot=snapshot,
            source_key=f"ADJUSTMENT:{locked.request_id}",
            source_record=locked,
            actor=actor,
            lines=(
                JournalLineSpec(
                    account_key=locked.debit_account.system_key,
                    debit=amount,
                    description=locked.reason,
                ),
                JournalLineSpec(
                    account_key=locked.credit_account.system_key,
                    credit=amount,
                    description=locked.reason,
                ),
            ),
        )
        journal = post_journal(journal, actor=actor)

    locked.state = FinancialAdjustmentRequest.STATE_POSTED
    locked.journal = journal
    locked.modified_by = actor
    locked.save(update_fields=("state", "journal", "modified_by", "modified_at"))
    _audit(
        locked,
        "POSTED",
        actor,
        reason=locked.reason,
        before={"state": FinancialAdjustmentRequest.STATE_APPROVED},
        after={"state": locked.state, "journal": journal.reference},
    )
    return locked
