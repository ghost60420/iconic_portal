from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import (
    BankReconciliation,
    BankReconciliationMatch,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)
from crm.services.financial_currency import money, resolve_currency_snapshot
from crm.services.financial_journal import JournalLineSpec, create_draft_journal, post_journal


class BankReconciliationError(ValueError):
    pass


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise BankReconciliationError("An authenticated actor is required.")
    return actor


def _line_account_amount(line, currency):
    if currency == "CAD":
        return money(line.cad_debit - line.cad_credit)
    if currency == "BDT":
        return money(line.bdt_debit - line.bdt_credit)
    return money(line.native_debit - line.native_credit)


def account_book_balance(account, as_of_date):
    fields = {
        "CAD": ("cad_debit", "cad_credit"),
        "BDT": ("bdt_debit", "bdt_credit"),
        "USD": ("native_debit", "native_credit"),
    }[account.currency]
    totals = JournalLine.objects.filter(
        journal__state__in=(JournalEntry.STATE_POSTED, JournalEntry.STATE_REVERSED),
        journal__journal_date__lte=as_of_date,
        account=account.gl_account,
    ).aggregate(debit=Sum(fields[0]), credit=Sum(fields[1]))
    return money((totals["debit"] or Decimal("0")) - (totals["credit"] or Decimal("0")))


@transaction.atomic
def match_statement_transaction(*, statement_line, journal_line, matched_amount, actor):
    user = _actor(actor)
    line = BankStatementLine.objects.select_for_update().select_related(
        "reconciliation__account"
    ).get(pk=statement_line.pk)
    book_line = JournalLine.objects.select_for_update().select_related("journal", "account").get(pk=journal_line.pk)
    if line.reconciliation.state not in (BankReconciliation.STATE_DRAFT, BankReconciliation.STATE_SUBMITTED):
        raise BankReconciliationError("Only draft or submitted reconciliations can be matched.")
    if book_line.account_id != line.reconciliation.account.gl_account_id:
        raise BankReconciliationError("Book line does not belong to the reconciled cash/bank account.")
    if book_line.journal.state not in (JournalEntry.STATE_POSTED, JournalEntry.STATE_REVERSED):
        raise BankReconciliationError("Only posted book transactions can be reconciled.")
    matched_amount = money(matched_amount)
    if matched_amount == 0:
        raise BankReconciliationError("Matched amount cannot be zero.")
    statement_total = (
        line.matches.aggregate(total=Sum("matched_amount"))["total"] or Decimal("0")
    )
    if abs(money(statement_total + matched_amount)) > abs(money(line.amount)):
        raise BankReconciliationError("Matches cannot exceed the statement transaction amount.")
    book_amount = _line_account_amount(book_line, line.reconciliation.account.currency)
    book_total = (
        BankReconciliationMatch.objects.filter(journal_line=book_line).aggregate(total=Sum("matched_amount"))["total"]
        or Decimal("0")
    )
    if abs(money(book_total + matched_amount)) > abs(book_amount):
        raise BankReconciliationError("Matches cannot exceed the book transaction amount.")
    match = BankReconciliationMatch.objects.create(
        statement_line=line,
        journal_line=book_line,
        matched_amount=matched_amount,
        created_by=user,
        modified_by=user,
    )
    statement_total = money(statement_total + matched_amount)
    BankStatementLine.objects.filter(pk=line.pk).update(
        is_matched=statement_total == money(line.amount),
        modified_by=user,
        modified_at=timezone.now(),
    )
    return match


@transaction.atomic
def submit_bank_reconciliation(reconciliation, *, actor):
    user = _actor(actor)
    locked = BankReconciliation.objects.select_for_update().prefetch_related("statement_lines__matches").get(
        pk=reconciliation.pk
    )
    if locked.state != BankReconciliation.STATE_DRAFT:
        raise BankReconciliationError("Only a draft reconciliation can be submitted.")
    statement_movement = money(sum((row.amount for row in locked.statement_lines.all()), Decimal("0")))
    calculated_closing = money(locked.statement_opening_balance + statement_movement)
    if calculated_closing != money(locked.statement_closing_balance):
        raise BankReconciliationError("Statement opening balance plus statement transactions does not equal closing balance.")
    now = timezone.now()
    BankReconciliation.objects.filter(pk=locked.pk).update(
        state=BankReconciliation.STATE_SUBMITTED,
        submitted_by=user,
        submitted_at=now,
        modified_by=user,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def approve_bank_reconciliation(reconciliation, *, actor):
    approver = _actor(actor)
    locked = BankReconciliation.objects.select_for_update().select_related("account").get(pk=reconciliation.pk)
    if locked.state != BankReconciliation.STATE_SUBMITTED:
        raise BankReconciliationError("Only a submitted reconciliation can be approved.")
    if locked.statement_lines.filter(is_matched=False).exists():
        raise BankReconciliationError("Every statement line must be matched before approval.")
    book_balance = account_book_balance(locked.account, locked.statement_end_date)
    difference = money(locked.statement_closing_balance - book_balance)
    if difference != 0:
        raise BankReconciliationError("Statement closing balance does not equal the book balance.")
    now = timezone.now()
    BankReconciliation.objects.filter(pk=locked.pk).update(
        state=BankReconciliation.STATE_APPROVED,
        book_closing_balance=book_balance,
        unexplained_difference=difference,
        reconciliation_date=timezone.localdate(),
        approved_by=approver,
        approved_at=now,
        modified_by=approver,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def record_company_transfer(
    *, source_account, destination_account, source_amount, destination_amount, transfer_date, reference, actor,
    source_rate_to_cad=None, source_rate_to_bdt=None, destination_rate_to_cad=None, destination_rate_to_bdt=None,
):
    user = _actor(actor)
    if source_account.pk == destination_account.pk:
        raise BankReconciliationError("Source and destination accounts must differ.")
    if not reference:
        raise BankReconciliationError("Company transfers require a reference.")
    source_snapshot = resolve_currency_snapshot(
        native_amount=source_amount,
        currency=source_account.currency,
        transaction_date=transfer_date,
        rate_to_cad=source_rate_to_cad,
        rate_to_bdt=source_rate_to_bdt,
        source_record=source_account,
        actor=user,
    )
    destination_snapshot = resolve_currency_snapshot(
        native_amount=destination_amount,
        currency=destination_account.currency,
        transaction_date=transfer_date,
        rate_to_cad=destination_rate_to_cad,
        rate_to_bdt=destination_rate_to_bdt,
        source_record=destination_account,
        actor=user,
    )
    outgoing = post_journal(
        create_draft_journal(
            journal_date=transfer_date,
            reference=f"FIN-XFER-OUT-{reference}",
            description=f"Company transfer {reference} outgoing",
            side=source_account.side,
            snapshot=source_snapshot,
            source_key=f"COMPANY-TRANSFER:{reference}:OUT",
            source_record=source_account,
            actor=user,
            lines=[
                JournalLineSpec("FX_CLEARING", debit=source_snapshot.native_amount),
                JournalLineSpec(source_account.gl_account.system_key, credit=source_snapshot.native_amount),
            ],
        ),
        actor=user,
    )
    incoming = post_journal(
        create_draft_journal(
            journal_date=transfer_date,
            reference=f"FIN-XFER-IN-{reference}",
            description=f"Company transfer {reference} incoming",
            side=destination_account.side,
            snapshot=destination_snapshot,
            source_key=f"COMPANY-TRANSFER:{reference}:IN",
            source_record=destination_account,
            actor=user,
            lines=[
                JournalLineSpec(destination_account.gl_account.system_key, debit=destination_snapshot.native_amount),
                JournalLineSpec("FX_CLEARING", credit=destination_snapshot.native_amount),
            ],
        ),
        actor=user,
    )
    difference_cad = money(source_snapshot.amount_cad - destination_snapshot.amount_cad)
    fx_journal = None
    if difference_cad:
        cad_snapshot = resolve_currency_snapshot(
            native_amount=abs(difference_cad),
            currency="CAD",
            transaction_date=transfer_date,
            rate_to_bdt=source_snapshot.rate_to_bdt if source_snapshot.currency == "CAD" else None,
            source_record=source_account,
            actor=user,
        )
        if difference_cad > 0:
            lines = [
                JournalLineSpec("FX_LOSS", debit=abs(difference_cad)),
                JournalLineSpec("FX_CLEARING", credit=abs(difference_cad)),
            ]
        else:
            lines = [
                JournalLineSpec("FX_CLEARING", debit=abs(difference_cad)),
                JournalLineSpec("FX_GAIN", credit=abs(difference_cad)),
            ]
        fx_journal = post_journal(
            create_draft_journal(
                journal_date=transfer_date,
                reference=f"FIN-XFER-FX-{reference}",
                description=f"Realized foreign exchange on company transfer {reference}",
                side=destination_account.side,
                snapshot=cad_snapshot,
                source_key=f"COMPANY-TRANSFER:{reference}:FX",
                source_record=destination_account,
                actor=user,
                lines=lines,
            ),
            actor=user,
        )
    return {"outgoing_journal": outgoing, "incoming_journal": incoming, "fx_journal": fx_journal, "difference_cad": difference_cad}
