from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import FinancialAccount, FinancialAuditEvent, FinancialPeriod, JournalEntry, JournalLine
from crm.services.financial_currency import CurrencySnapshot, money


class JournalError(ValueError):
    pass


class DuplicateJournal(JournalError):
    pass


@dataclass(frozen=True)
class JournalLineSpec:
    account_key: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str = ""
    customer: object = None
    supplier: object = None
    production_order: object = None
    department: object = None


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise JournalError("An authenticated actor is required for financial posting.")
    return actor


def _audit(record, action, actor, *, reason="", before=None, after=None, source_reference=""):
    content_type = ContentType.objects.get_for_model(record, for_concrete_model=False)
    return FinancialAuditEvent.objects.create(
        content_type=content_type,
        object_id=record.pk,
        action=action,
        reason=reason or "",
        before_value=before or {},
        after_value=after or {},
        source_reference=source_reference or "",
        actor=actor,
    )


def _open_period(journal_date, side):
    periods = FinancialPeriod.objects.filter(start_date__lte=journal_date, end_date__gte=journal_date)
    period = periods.filter(side=side).first() or periods.filter(side="").first()
    if not period:
        raise JournalError("No Financial Core period is configured for the journal date and side.")
    if period.state != FinancialPeriod.STATE_OPEN:
        raise JournalError(f"Financial period '{period.name}' is {period.state.lower()}.")
    return period


def _validate_specs(specs):
    specs = tuple(specs)
    if len(specs) < 2:
        raise JournalError("A journal requires at least two lines.")
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    keys = set()
    for spec in specs:
        debit = money(spec.debit) if spec.debit else Decimal("0")
        credit = money(spec.credit) if spec.credit else Decimal("0")
        if (debit > 0) == (credit > 0):
            raise JournalError("Each journal line must contain exactly one positive debit or credit.")
        total_debit += debit
        total_credit += credit
        keys.add((spec.account_key or "").upper().strip())
    if money(total_debit) != money(total_credit):
        raise JournalError("Native journal debits must equal credits.")
    if total_debit <= 0:
        raise JournalError("Journal total must be greater than zero.")
    return specs, keys


@transaction.atomic
def create_draft_journal(
    *,
    journal_date,
    reference,
    description,
    side,
    snapshot: CurrencySnapshot,
    source_key,
    lines,
    actor,
    source_record=None,
    migration_batch="",
) -> JournalEntry:
    creator = _actor(actor)
    key = (source_key or "").strip()
    if not key:
        raise JournalError("A journal source key is required.")
    if JournalEntry.objects.filter(source_key=key).exists():
        raise DuplicateJournal(f"Journal source key '{key}' already exists.")
    specs, account_keys = _validate_specs(lines)
    accounts = {
        account.system_key: account
        for account in FinancialAccount.objects.filter(system_key__in=account_keys, is_active=True)
    }
    missing = sorted(account_keys - set(accounts))
    if missing:
        raise JournalError("Missing Financial Core accounts: " + ", ".join(missing))

    source_content_type = None
    source_object_id = None
    if source_record is not None:
        if not getattr(source_record, "pk", None):
            raise JournalError("A journal source record must be saved first.")
        source_content_type = ContentType.objects.get_for_model(source_record, for_concrete_model=False)
        source_object_id = source_record.pk

    journal = JournalEntry.objects.create(
        journal_date=journal_date,
        reference=(reference or "").strip(),
        description=(description or "").strip(),
        side=(side or "").upper().strip(),
        currency=snapshot.currency,
        rate_to_cad=snapshot.rate_to_cad,
        rate_to_bdt=snapshot.rate_to_bdt,
        period=_open_period(journal_date, side),
        source_content_type=source_content_type,
        source_object_id=source_object_id,
        source_key=key,
        migration_batch=(migration_batch or "").strip(),
        created_by=creator,
        modified_by=creator,
    )
    journal_lines = []
    for number, spec in enumerate(specs, start=1):
        debit = money(spec.debit) if spec.debit else Decimal("0")
        credit = money(spec.credit) if spec.credit else Decimal("0")
        journal_lines.append(
            JournalLine(
                journal=journal,
                line_number=number,
                account=accounts[spec.account_key.upper().strip()],
                description=(spec.description or "").strip(),
                native_debit=debit,
                native_credit=credit,
                cad_debit=money(debit * snapshot.rate_to_cad) if debit else Decimal("0"),
                cad_credit=money(credit * snapshot.rate_to_cad) if credit else Decimal("0"),
                bdt_debit=money(debit * snapshot.rate_to_bdt) if debit else Decimal("0"),
                bdt_credit=money(credit * snapshot.rate_to_bdt) if credit else Decimal("0"),
                customer=spec.customer,
                supplier=spec.supplier,
                production_order=spec.production_order,
                department=spec.department,
            )
        )
    JournalLine.objects.bulk_create(journal_lines)
    _audit(
        journal,
        "CREATED",
        creator,
        after={"state": journal.state, "line_count": len(journal_lines), "currency": snapshot.currency},
        source_reference=key,
    )
    return journal


def _validate_balances(journal):
    totals = journal.lines.aggregate(
        native_debit=Sum("native_debit"),
        native_credit=Sum("native_credit"),
        cad_debit=Sum("cad_debit"),
        cad_credit=Sum("cad_credit"),
        bdt_debit=Sum("bdt_debit"),
        bdt_credit=Sum("bdt_credit"),
    )
    if journal.lines.count() < 2:
        raise JournalError("A journal requires at least two lines.")
    for prefix in ("native", "cad", "bdt"):
        debit = money(totals[f"{prefix}_debit"] or 0)
        credit = money(totals[f"{prefix}_credit"] or 0)
        if debit != credit:
            raise JournalError(f"{prefix.upper()} journal debits must equal credits.")
        if debit <= 0:
            raise JournalError("Journal total must be greater than zero.")
    return totals


@transaction.atomic
def post_journal(journal, *, actor) -> JournalEntry:
    approver = _actor(actor)
    locked = JournalEntry.objects.select_for_update().select_related("period").get(pk=journal.pk)
    if locked.state != JournalEntry.STATE_DRAFT:
        raise JournalError("Only a draft journal can be posted.")
    if locked.period.state != FinancialPeriod.STATE_OPEN:
        raise JournalError(f"Financial period '{locked.period.name}' is not open.")
    if not locked.period.start_date <= locked.journal_date <= locked.period.end_date:
        raise JournalError("Journal date is outside its Financial Core period.")
    totals = _validate_balances(locked)
    posted_at = timezone.now()
    JournalEntry.objects.filter(pk=locked.pk, state=JournalEntry.STATE_DRAFT).update(
        state=JournalEntry.STATE_POSTED,
        submitted_by=locked.created_by,
        submitted_at=posted_at,
        approved_by=approver,
        approved_at=posted_at,
        posted_at=posted_at,
        modified_by=approver,
        modified_at=posted_at,
    )
    locked.refresh_from_db()
    _audit(
        locked,
        "POSTED",
        approver,
        before={"state": JournalEntry.STATE_DRAFT},
        after={"state": locked.state, "native_total": str(totals["native_debit"])},
        source_reference=locked.source_key,
    )
    return locked


@transaction.atomic
def reverse_journal(journal, *, actor, reason, reversal_date=None) -> JournalEntry:
    approver = _actor(actor)
    reason = (reason or "").strip()
    if not reason:
        raise JournalError("A reversal reason is required.")
    original = JournalEntry.objects.select_for_update().prefetch_related("lines").get(pk=journal.pk)
    if original.state != JournalEntry.STATE_POSTED:
        raise JournalError("Only a posted, unreversed journal can be reversed.")
    reversal_date = reversal_date or original.journal_date
    snapshot = CurrencySnapshot(
        native_amount=money(sum((line.native_debit for line in original.lines.all()), Decimal("0"))),
        currency=original.currency,
        rate_to_cad=original.rate_to_cad,
        rate_to_bdt=original.rate_to_bdt,
        amount_cad=money(sum((line.cad_debit for line in original.lines.all()), Decimal("0"))),
        amount_bdt=money(sum((line.bdt_debit for line in original.lines.all()), Decimal("0"))),
        rate_path=("REVERSAL",),
    )
    specs = [
        JournalLineSpec(
            account_key=line.account.system_key,
            debit=line.native_credit,
            credit=line.native_debit,
            description=f"Reversal: {line.description or original.description}",
            customer=line.customer,
            supplier=line.supplier,
            production_order=line.production_order,
            department=line.department,
        )
        for line in original.lines.select_related("account", "customer", "supplier", "production_order", "department")
    ]
    reversal = create_draft_journal(
        journal_date=reversal_date,
        reference=f"REV-{original.reference}",
        description=f"Reversal of {original.reference}: {reason}",
        side=original.side,
        snapshot=snapshot,
        source_key=f"REVERSAL:{original.source_key}",
        lines=specs,
        actor=approver,
        source_record=original.source_record,
        migration_batch=original.migration_batch,
    )
    JournalEntry.objects.filter(pk=reversal.pk).update(reverses=original)
    reversal.refresh_from_db()
    reversal = post_journal(reversal, actor=approver)
    JournalEntry.objects.filter(pk=original.pk).update(
        state=JournalEntry.STATE_REVERSED,
        modified_by=approver,
        modified_at=timezone.now(),
        change_reason=reason,
    )
    original.refresh_from_db()
    _audit(
        original,
        "REVERSED",
        approver,
        reason=reason,
        before={"state": JournalEntry.STATE_POSTED},
        after={"state": original.state, "reversal_id": reversal.pk},
        source_reference=original.source_key,
    )
    return reversal


def validate_journal_model(journal):
    try:
        journal.full_clean()
    except ValidationError as exc:
        raise JournalError(str(exc)) from exc
    return _validate_balances(journal)
