import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from crm.models import (
    ExpenseRecord,
    JournalEntry,
    PayrollBatch,
    RecurringExpenseTemplate,
    SupplierBill,
)
from crm.services.financial_currency import money, resolve_currency_snapshot
from crm.services.financial_journal import JournalLineSpec, create_draft_journal, post_journal, reverse_journal
from crm.services.payables_ledger import approve_supplier_bill


class ExpenseManagementError(ValueError):
    pass


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ExpenseManagementError("An authenticated actor is required.")
    return actor


@transaction.atomic
def submit_expense(expense, *, actor):
    submitter = _actor(actor)
    locked = ExpenseRecord.objects.select_for_update().get(pk=expense.pk)
    if locked.approval_status != ExpenseRecord.APPROVAL_DRAFT:
        raise ExpenseManagementError("Only a draft expense can be submitted.")
    locked.full_clean()
    now = timezone.now()
    ExpenseRecord.objects.filter(pk=locked.pk).update(
        approval_status=ExpenseRecord.APPROVAL_PENDING,
        submitted_by=submitter,
        submitted_at=now,
        modified_by=submitter,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def approve_expense(expense, *, actor, rate_to_cad=None, rate_to_bdt=None):
    approver = _actor(actor)
    locked = ExpenseRecord.objects.select_for_update().select_related(
        "vendor", "category__default_account", "department", "payment_account", "production_order"
    ).get(pk=expense.pk)
    if locked.approval_status == ExpenseRecord.APPROVAL_APPROVED:
        return locked
    if locked.approval_status not in (ExpenseRecord.APPROVAL_DRAFT, ExpenseRecord.APPROVAL_PENDING):
        raise ExpenseManagementError("Only a draft or pending expense can be approved.")
    locked.full_clean()
    snapshot = resolve_currency_snapshot(
        native_amount=locked.total_amount,
        currency=locked.currency,
        transaction_date=locked.expense_date,
        rate_to_cad=rate_to_cad or locked.rate_to_cad,
        rate_to_bdt=rate_to_bdt or locked.rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    now = timezone.now()
    if locked.payment_status == ExpenseRecord.PAYMENT_PAID:
        if not locked.payment_account_id:
            raise ExpenseManagementError("A paid expense requires a cash or bank account.")
        if locked.payment_account.currency != snapshot.currency:
            raise ExpenseManagementError("Paid expense currency must match the cash/bank account currency.")
        journal = post_journal(
            create_draft_journal(
                journal_date=locked.expense_date,
                reference=f"FIN-EXP-{locked.expense_number}",
                description=f"Expense {locked.expense_number}: {locked.description}",
                side=locked.side,
                snapshot=snapshot,
                source_key=f"EXPENSE:{locked.pk}:PAID",
                source_record=locked,
                actor=approver,
                lines=[
                    JournalLineSpec(
                        locked.category.default_account.system_key,
                        debit=snapshot.native_amount,
                        supplier=locked.vendor,
                        production_order=locked.production_order,
                        department=locked.department,
                    ),
                    JournalLineSpec(
                        locked.payment_account.gl_account.system_key,
                        credit=snapshot.native_amount,
                        supplier=locked.vendor,
                        production_order=locked.production_order,
                        department=locked.department,
                    ),
                ],
            ),
            actor=approver,
        )
        supplier_bill = None
    else:
        if not locked.vendor_id:
            raise ExpenseManagementError("An unpaid expense requires a configured supplier/vendor.")
        supplier_bill = SupplierBill.objects.create(
            supplier=locked.vendor,
            bill_number=locked.expense_number,
            bill_date=locked.expense_date,
            due_date=locked.due_date or locked.expense_date,
            currency=snapshot.currency,
            amount_before_tax=locked.amount_before_tax,
            tax_amount=locked.tax_amount,
            total_amount=snapshot.native_amount,
            rate_to_cad=snapshot.rate_to_cad,
            rate_to_bdt=snapshot.rate_to_bdt,
            amount_cad=snapshot.amount_cad,
            amount_bdt=snapshot.amount_bdt,
            expense_account=locked.category.default_account,
            production_order=locked.production_order,
            department=locked.department,
            side=locked.side,
            description=locked.description,
            approval_status=SupplierBill.APPROVAL_PENDING,
            payment_status=SupplierBill.PAYMENT_UNPAID,
            remaining_amount=snapshot.native_amount,
            created_by=locked.created_by or approver,
            modified_by=approver,
            submitted_by=locked.submitted_by or approver,
            submitted_at=locked.submitted_at or now,
        )
        supplier_bill = approve_supplier_bill(supplier_bill, actor=approver)
        journal = supplier_bill.payable_journal

    ExpenseRecord.objects.filter(pk=locked.pk).update(
        approval_status=ExpenseRecord.APPROVAL_APPROVED,
        rate_to_cad=snapshot.rate_to_cad,
        rate_to_bdt=snapshot.rate_to_bdt,
        amount_cad=snapshot.amount_cad,
        amount_bdt=snapshot.amount_bdt,
        journal=journal,
        supplier_bill=supplier_bill,
        approved_by=approver,
        approved_at=now,
        modified_by=approver,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def reverse_expense(expense, *, actor, reason, reversal_date=None):
    approver = _actor(actor)
    locked = ExpenseRecord.objects.select_for_update().select_related("journal", "supplier_bill").get(pk=expense.pk)
    if locked.approval_status != ExpenseRecord.APPROVAL_APPROVED:
        raise ExpenseManagementError("Only an approved expense can be reversed.")
    if locked.supplier_bill_id:
        from crm.services.payables_ledger import reverse_supplier_bill

        reverse_supplier_bill(locked.supplier_bill, actor=approver, reason=reason, reversal_date=reversal_date)
    elif locked.journal_id:
        reverse_journal(locked.journal, actor=approver, reason=reason, reversal_date=reversal_date)
    else:
        raise ExpenseManagementError("Approved expense has no supporting journal.")
    ExpenseRecord.objects.filter(pk=locked.pk).update(
        approval_status=ExpenseRecord.APPROVAL_REVERSED,
        change_reason=reason,
        modified_by=approver,
        modified_at=timezone.now(),
    )
    locked.refresh_from_db()
    return locked


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_occurrence(value, frequency):
    if frequency == RecurringExpenseTemplate.FREQ_WEEKLY:
        return value + timedelta(days=7)
    if frequency == RecurringExpenseTemplate.FREQ_MONTHLY:
        return _add_months(value, 1)
    if frequency == RecurringExpenseTemplate.FREQ_QUARTERLY:
        return _add_months(value, 3)
    if frequency == RecurringExpenseTemplate.FREQ_ANNUAL:
        return _add_months(value, 12)
    raise ExpenseManagementError(f"Unsupported recurring frequency '{frequency}'.")


@transaction.atomic
def generate_recurring_expense_drafts(*, through_date, actor=None, template_ids=None):
    creator = _actor(actor) if actor else None
    templates = RecurringExpenseTemplate.objects.select_for_update().filter(is_active=True)
    if template_ids is not None:
        templates = templates.filter(pk__in=tuple(template_ids))
    created = []
    for template in templates.select_related("vendor", "category", "department"):
        occurrence = template.start_date
        if template.last_generated_for:
            occurrence = _next_occurrence(template.last_generated_for, template.frequency)
        while occurrence <= through_date and (not template.end_date or occurrence <= template.end_date):
            due_date = occurrence.replace(day=min(template.due_day, calendar.monthrange(occurrence.year, occurrence.month)[1]))
            expense_number = f"REC-{template.pk}-{occurrence:%Y%m%d}"
            expense, was_created = ExpenseRecord.objects.get_or_create(
                expense_number=expense_number,
                defaults={
                    "expense_date": occurrence,
                    "vendor": template.vendor,
                    "vendor_name": template.vendor_name,
                    "category": template.category,
                    "department": template.department,
                    "side": template.side,
                    "currency": template.currency,
                    "amount_before_tax": template.expected_amount,
                    "tax_amount": Decimal("0"),
                    "total_amount": template.expected_amount,
                    "rate_to_cad": Decimal("0"),
                    "rate_to_bdt": Decimal("0"),
                    "amount_cad": Decimal("0"),
                    "amount_bdt": Decimal("0"),
                    "payment_status": ExpenseRecord.PAYMENT_UNPAID,
                    "due_date": due_date,
                    "description": template.description or template.name,
                    "business_purpose": template.name,
                    "approval_status": ExpenseRecord.APPROVAL_DRAFT,
                    "is_recurring_instance": True,
                    "recurring_template": template,
                    "created_by": creator,
                    "modified_by": creator,
                },
            )
            if was_created:
                created.append(expense)
            template.last_generated_for = occurrence
            template.modified_by = creator
            template.save(update_fields=("last_generated_for", "modified_by", "modified_at"))
            occurrence = _next_occurrence(occurrence, template.frequency)
    return created


@transaction.atomic
def approve_payroll_batch(batch, *, actor, rate_to_cad=None, rate_to_bdt=None):
    approver = _actor(actor)
    locked = PayrollBatch.objects.select_for_update().select_related("department").get(pk=batch.pk)
    if locked.state == PayrollBatch.STATE_APPROVED:
        return locked
    if locked.state not in (PayrollBatch.STATE_DRAFT, PayrollBatch.STATE_PENDING):
        raise ExpenseManagementError("Only draft or pending payroll can be approved.")
    lines = list(locked.private_lines.all())
    if not lines:
        raise ExpenseManagementError("Payroll batch has no employee lines.")
    base = money(sum((row.base_salary + row.commission for row in lines), Decimal("0")))
    overtime = money(sum((row.overtime for row in lines), Decimal("0")))
    bonus = money(sum((row.bonus for row in lines), Decimal("0")))
    employer = money(sum((row.employer_cost for row in lines), Decimal("0")))
    deductions = money(sum((row.deductions for row in lines), Decimal("0")))
    net = money(sum((row.net_pay for row in lines), Decimal("0")))
    total_debit = base + overtime + bonus + employer
    total_credit = net + deductions + employer
    if total_debit != total_credit:
        raise ExpenseManagementError(
            "Payroll does not reconcile: gross plus employer cost must equal net pay, deductions, and employer liabilities."
        )
    snapshot = resolve_currency_snapshot(
        native_amount=total_debit,
        currency=locked.currency,
        transaction_date=locked.period_end,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    salary_key = "CANADA_SALARIES" if locked.side == "CA" else "BANGLADESH_SALARIES"
    debit_specs = [JournalLineSpec(salary_key, debit=base, department=locked.department)]
    if overtime:
        debit_specs.append(JournalLineSpec("OVERTIME", debit=overtime, department=locked.department))
    if bonus:
        debit_specs.append(JournalLineSpec("BONUSES", debit=bonus, department=locked.department))
    if employer:
        debit_specs.append(JournalLineSpec("PAYROLL_TAXES", debit=employer, department=locked.department))
    credit_specs = [JournalLineSpec("PAYROLL_PAYABLE", credit=net, department=locked.department)]
    if deductions + employer:
        credit_specs.append(JournalLineSpec("TAXES_PAYABLE", credit=deductions + employer, department=locked.department))
    journal = post_journal(
        create_draft_journal(
            journal_date=locked.period_end,
            reference=f"FIN-PAYROLL-{locked.reference}",
            description=f"Payroll accrual {locked.reference}",
            side=locked.side,
            snapshot=snapshot,
            source_key=f"PAYROLL:{locked.pk}:ACCRUAL",
            source_record=locked,
            actor=approver,
            lines=[*debit_specs, *credit_specs],
        ),
        actor=approver,
    )
    now = timezone.now()
    PayrollBatch.objects.filter(pk=locked.pk).update(
        state=PayrollBatch.STATE_APPROVED,
        journal=journal,
        submitted_by=locked.submitted_by or locked.created_by or approver,
        submitted_at=locked.submitted_at or now,
        approved_by=approver,
        approved_at=now,
        modified_by=approver,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def pay_payroll_batch(batch, *, actor, payment_date, payment_account, rate_to_cad=None, rate_to_bdt=None):
    approver = _actor(actor)
    locked = PayrollBatch.objects.select_for_update().prefetch_related("private_lines").get(pk=batch.pk)
    if locked.state != PayrollBatch.STATE_APPROVED:
        raise ExpenseManagementError("Only approved payroll can be paid.")
    net = money(sum((row.net_pay for row in locked.private_lines.all()), Decimal("0")))
    snapshot = resolve_currency_snapshot(
        native_amount=net,
        currency=locked.currency,
        transaction_date=payment_date,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        source_record=locked,
        actor=approver,
    )
    if payment_account.currency != snapshot.currency:
        raise ExpenseManagementError("Payroll payment currency must match the cash/bank account currency.")
    post_journal(
        create_draft_journal(
            journal_date=payment_date,
            reference=f"FIN-PAYROLL-PAY-{locked.reference}",
            description=f"Payroll payment {locked.reference}",
            side=locked.side,
            snapshot=snapshot,
            source_key=f"PAYROLL:{locked.pk}:PAYMENT",
            source_record=locked,
            actor=approver,
            lines=[
                JournalLineSpec("PAYROLL_PAYABLE", debit=net, department=locked.department),
                JournalLineSpec(payment_account.gl_account.system_key, credit=net, department=locked.department),
            ],
        ),
        actor=approver,
    )
    PayrollBatch.objects.filter(pk=locked.pk).update(
        state=PayrollBatch.STATE_PAID,
        payment_date=payment_date,
        payment_account=payment_account,
        modified_by=approver,
        modified_at=timezone.now(),
    )
    locked.refresh_from_db()
    return locked
