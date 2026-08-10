from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from crm.models import (
    ExpenseCategory,
    ExpenseRecord,
    FinanceOperation,
    FinancialAccount,
    FinancialAuditEvent,
    InvoicePayment,
    InventoryItem,
    InventoryMovement,
    JournalEntry,
    PayrollBatch,
    PayrollLine,
    ProductionCostRecord,
    SupplierBill,
)
from crm.services.chart_of_accounts import account_by_key
from crm.services.expense_management import (
    approve_expense,
    approve_payroll_batch,
    pay_payroll_batch,
    submit_expense,
)
from crm.services.factory_timeline import record_actual_factory_timeline, save_estimated_factory_timeline
from crm.services.financial_currency import CurrencySnapshot, money, resolve_currency_snapshot
from crm.services.financial_journal import JournalLineSpec, create_draft_journal, post_journal
from crm.services.payables_ledger import approve_supplier_bill, record_supplier_payment
from crm.services.payment_reconciliation import record_invoice_payment
from crm.services.production_costs import approve_production_cost
from crm.services.receivable_accounting import (
    _invoice_revenue_key,
    apply_customer_credit_note,
    invoice_outstanding,
    record_customer_receipt,
    record_customer_refund,
)


class FinanceOperationError(ValueError):
    pass


class FinanceOperationWritesDisabled(FinanceOperationError):
    pass


WORKFLOWS = (
    ("customer-payment", FinanceOperation.TYPE_CUSTOMER_PAYMENT, "Record Customer Payment", "hand-coins", "Customer"),
    ("customer-refund", FinanceOperation.TYPE_CUSTOMER_REFUND, "Create Customer Refund", "undo-2", "Customer"),
    ("customer-credit-note", FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE, "Create Credit Note", "file-minus-2", "Customer"),
    ("customer-credit", FinanceOperation.TYPE_CUSTOMER_CREDIT, "Record Customer Credit", "badge-dollar-sign", "Customer"),
    ("supplier-bill", FinanceOperation.TYPE_SUPPLIER_BILL, "Add Supplier Bill", "file-plus-2", "Supplier"),
    ("supplier-payment", FinanceOperation.TYPE_SUPPLIER_PAYMENT, "Record Supplier Payment", "send", "Supplier"),
    ("expense", FinanceOperation.TYPE_COMPANY_EXPENSE, "Add Company Expense", "receipt", "Expense"),
    ("utility", FinanceOperation.TYPE_UTILITY_BILL, "Add Utility Bill", "zap", "Expense"),
    ("payroll", FinanceOperation.TYPE_PAYROLL, "Add Payroll Expense", "users", "Payroll"),
    ("production-cost", FinanceOperation.TYPE_PRODUCTION_COST, "Add Production Cost", "factory", "Production"),
    ("factory-daily-cost", FinanceOperation.TYPE_FACTORY_DAILY_COST, "Add Factory Daily Cost", "calendar-clock", "Production"),
    ("bank-deposit", FinanceOperation.TYPE_BANK_DEPOSIT, "Record Bank Deposit", "circle-arrow-down", "Bank"),
    ("bank-withdrawal", FinanceOperation.TYPE_BANK_WITHDRAWAL, "Record Bank Withdrawal", "circle-arrow-up", "Bank"),
    ("cash-deposit", FinanceOperation.TYPE_CASH_DEPOSIT, "Record Cash Deposit", "banknote-arrow-down", "Bank"),
    ("cash-withdrawal", FinanceOperation.TYPE_CASH_WITHDRAWAL, "Record Cash Withdrawal", "banknote-arrow-up", "Bank"),
    ("money-transfer", FinanceOperation.TYPE_ACCOUNT_TRANSFER, "Record Money Transfer", "arrow-left-right", "Bank"),
    ("bank-fee", FinanceOperation.TYPE_BANK_FEE, "Record Bank Fee", "landmark", "Bank"),
    ("processor-fee", FinanceOperation.TYPE_PROCESSOR_FEE, "Record Processor Fee", "credit-card", "Bank"),
    ("owner-investment", FinanceOperation.TYPE_OWNER_INVESTMENT, "Record Owner Investment", "circle-plus", "Owner and Loan"),
    ("owner-withdrawal", FinanceOperation.TYPE_OWNER_WITHDRAWAL, "Record Owner Withdrawal", "circle-minus", "Owner and Loan"),
    ("loan-received", FinanceOperation.TYPE_LOAN_RECEIVED, "Record Loan Received", "handshake", "Owner and Loan"),
    ("loan-principal", FinanceOperation.TYPE_LOAN_PRINCIPAL, "Record Loan Principal Payment", "landmark", "Owner and Loan"),
    ("loan-interest", FinanceOperation.TYPE_LOAN_INTEREST, "Record Loan Interest Payment", "percent", "Owner and Loan"),
    ("shareholder-advance", FinanceOperation.TYPE_SHAREHOLDER_ADVANCE, "Record Shareholder Advance", "arrow-up-right", "Owner and Loan"),
    ("shareholder-repayment", FinanceOperation.TYPE_SHAREHOLDER_REPAYMENT, "Record Shareholder Repayment", "arrow-down-left", "Owner and Loan"),
    ("asset-purchase", FinanceOperation.TYPE_ASSET_PURCHASE, "Record Asset Purchase", "monitor-cog", "Assets"),
    ("inventory-adjustment", FinanceOperation.TYPE_INVENTORY_ADJUSTMENT, "Record Inventory Adjustment", "package-check", "Inventory"),
)

WORKFLOW_BY_SLUG = {
    slug: {"slug": slug, "operation_type": operation_type, "title": title, "icon": icon, "group": group}
    for slug, operation_type, title, icon, group in WORKFLOWS
}
WORKFLOW_BY_TYPE = {row[1]: WORKFLOW_BY_SLUG[row[0]] for row in WORKFLOWS}

HIGH_RISK_TYPES = {
    FinanceOperation.TYPE_CUSTOMER_REFUND,
    FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE,
    FinanceOperation.TYPE_CUSTOMER_CREDIT,
    FinanceOperation.TYPE_SUPPLIER_PAYMENT,
    FinanceOperation.TYPE_PAYROLL,
    FinanceOperation.TYPE_BANK_WITHDRAWAL,
    FinanceOperation.TYPE_CASH_WITHDRAWAL,
    FinanceOperation.TYPE_ACCOUNT_TRANSFER,
    FinanceOperation.TYPE_OWNER_INVESTMENT,
    FinanceOperation.TYPE_OWNER_WITHDRAWAL,
    FinanceOperation.TYPE_LOAN_RECEIVED,
    FinanceOperation.TYPE_LOAN_PRINCIPAL,
    FinanceOperation.TYPE_LOAN_INTEREST,
    FinanceOperation.TYPE_SHAREHOLDER_ADVANCE,
    FinanceOperation.TYPE_SHAREHOLDER_REPAYMENT,
    FinanceOperation.TYPE_ASSET_PURCHASE,
    FinanceOperation.TYPE_INVENTORY_ADJUSTMENT,
}

UTILITY_ACCOUNT_KEYS = {
    "ELECTRICITY": "ELECTRICITY",
    "HYDRO": "HYDRO",
    "WATER": "WATER",
    "GAS": "GAS",
    "INTERNET": "INTERNET",
    "TELEPHONE": "TELEPHONE",
    "GENERATOR_FUEL": "FUEL",
    "OTHER_UTILITY": "MISCELLANEOUS_EXPENSE",
}

PRODUCTION_ACCOUNT_KEYS = {
    "FABRIC": "COGS_FABRIC",
    "TRIMS": "COGS_TRIMS",
    "CUTTING": "COGS_CUTTING",
    "SEWING": "COGS_SEWING",
    "PRINTING": "COGS_PRINTING",
    "EMBROIDERY": "COGS_EMBROIDERY",
    "WASHING": "COGS_WASHING",
    "PACKING": "COGS_PACKING",
    "QUALITY_CONTROL": "COGS_OTHER_DIRECT",
    "PRODUCTION_LABOR": "COGS_PRODUCTION_LABOR",
    "SHIPPING": "COGS_PRODUCTION_SHIPPING",
    "DUTY": "COGS_DUTY",
    "REWORK": "COGS_OTHER_DIRECT",
    "WASTE": "COGS_OTHER_DIRECT",
    "OTHER_DIRECT": "COGS_OTHER_DIRECT",
}

ASSET_ACCOUNT_KEYS = {
    "SEWING_MACHINE": "MACHINERY",
    "CUTTING_MACHINE": "MACHINERY",
    "COMPUTER": "EQUIPMENT",
    "VEHICLE": "VEHICLES",
    "OFFICE_FURNITURE": "EQUIPMENT",
    "FACTORY_EQUIPMENT": "EQUIPMENT",
    "OTHER": "OTHER_ASSETS",
}


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise FinanceOperationError("An authenticated user is required.")
    return actor


def _decimal(value, default="0"):
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FinanceOperationError("The operation contains an invalid numeric value.") from exc


def workflow_definition(slug):
    try:
        return WORKFLOW_BY_SLUG[slug]
    except KeyError as exc:
        raise FinanceOperationError("Unknown finance workflow.") from exc


def operation_number():
    return f"FOP-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"


def audit_operation(operation, action, actor, *, reason="", before=None, after=None):
    content_type = ContentType.objects.get_for_model(operation, for_concrete_model=False)
    return FinancialAuditEvent.objects.create(
        content_type=content_type,
        object_id=operation.pk,
        action=action,
        reason=(reason or "").strip(),
        before_value=before or {},
        after_value=after or {},
        source_reference=operation.operation_number,
        actor=_actor(actor),
    )


def _snapshot_from_operation(operation):
    return CurrencySnapshot(
        native_amount=money(operation.total_amount),
        currency=operation.currency,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        amount_cad=operation.amount_cad,
        amount_bdt=operation.amount_bdt,
        rate_path=("FINANCE_OPERATION_SNAPSHOT",),
    )


def _posting_reference(operation, suffix=""):
    base = (operation.reference or "").strip() or operation.operation_number
    return f"{base}{suffix}"


def _line(account_key, *, debit=0, credit=0, description=""):
    return {
        "account_key": account_key,
        "debit": str(money(debit)) if debit else "0.00",
        "credit": str(money(credit)) if credit else "0.00",
        "description": description,
    }


def _entry(description, lines, *, posting_date=None, currency="", side=""):
    return {
        "description": description,
        "posting_date": str(posting_date or ""),
        "currency": currency,
        "side": side,
        "lines": lines,
    }


def _account_key_from_category(operation):
    if not operation.expense_category_id:
        raise FinanceOperationError("An approved expense category is required.")
    key = operation.expense_category.default_account.system_key
    if not key:
        raise FinanceOperationError("The selected expense category has no General Ledger account mapping.")
    return key


def _transfer_preview(operation):
    if not operation.from_account_id or not operation.to_account_id:
        raise FinanceOperationError("A company transfer requires both source and destination accounts.")
    destination_amount = money(operation.details.get("destination_amount") or operation.total_amount)
    destination_currency = operation.details.get("destination_currency") or operation.to_account.currency
    if destination_currency != operation.to_account.currency:
        raise FinanceOperationError("Destination currency must match the destination company account.")
    is_cross_country = operation.from_account.side != operation.to_account.side
    if operation.from_account.currency == operation.to_account.currency and not is_cross_country:
        entries = [
            _entry(
                "Company account transfer",
                [
                    _line(operation.to_account.gl_account.system_key, debit=operation.total_amount),
                    _line(operation.from_account.gl_account.system_key, credit=operation.total_amount),
                ],
                posting_date=operation.transaction_date,
                currency=operation.currency,
                side=operation.side,
            )
        ]
    else:
        entries = [
            _entry(
                "Transfer out through foreign exchange clearing",
                [
                    _line("FX_CLEARING", debit=operation.total_amount),
                    _line(operation.from_account.gl_account.system_key, credit=operation.total_amount),
                ],
                posting_date=operation.transaction_date,
                currency=operation.currency,
                side=operation.from_account.side,
            ),
            _entry(
                "Transfer in through foreign exchange clearing",
                [
                    _line(operation.to_account.gl_account.system_key, debit=destination_amount),
                    _line("FX_CLEARING", credit=destination_amount),
                ],
                posting_date=operation.transaction_date,
                currency=destination_currency,
                side=operation.to_account.side,
            ),
        ]
    transfer_fee_value = _decimal(operation.details.get("transfer_fee") or 0)
    if transfer_fee_value < 0:
        raise FinanceOperationError("Transfer fee cannot be negative.")
    transfer_fee = money(transfer_fee_value)
    if transfer_fee:
        fee_currency = operation.details.get("fee_currency") or operation.currency
        if fee_currency != operation.currency:
            raise FinanceOperationError("Transfer fee currency must match the charged source account.")
        entries.append(
            _entry(
                "Transfer service fee",
                [
                    _line(operation.details.get("fee_account_key") or "BANK_FEES", debit=transfer_fee),
                    _line(operation.from_account.gl_account.system_key, credit=transfer_fee),
                ],
                posting_date=operation.transaction_date,
                currency=fee_currency,
                side=operation.from_account.side,
            )
        )
    return entries


def build_posting_preview(operation):
    operation_type = operation.operation_type
    amount = money(operation.total_amount)
    entries = []
    explanation = ""

    if operation_type in {FinanceOperation.TYPE_CUSTOMER_PAYMENT, FinanceOperation.TYPE_CUSTOMER_CREDIT}:
        if not operation.to_account_id:
            raise FinanceOperationError("Customer receipts require a destination bank or cash account.")
        allocation = Decimal("0")
        if operation.invoice_id:
            allocation = min(amount, invoice_outstanding(operation.invoice))
        credit = amount - allocation
        lines = [_line(operation.to_account.gl_account.system_key, debit=amount)]
        if allocation:
            lines.append(_line("ACCOUNTS_RECEIVABLE", credit=allocation))
        if credit:
            lines.append(_line("CUSTOMER_DEPOSITS", credit=credit))
        entries.append(_entry("Customer receipt", lines, posting_date=operation.transaction_date, currency=operation.currency))
        explanation = "Increase the selected cash or bank account and reduce the invoice balance; any approved excess becomes customer credit."
    elif operation_type == FinanceOperation.TYPE_CUSTOMER_REFUND:
        offset = "ACCOUNTS_RECEIVABLE" if operation.invoice_id else "CUSTOMER_DEPOSITS"
        entries.append(_entry(
            "Customer refund",
            [_line(offset, debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Reduce the customer settlement or credit and reduce the selected cash or bank account."
    elif operation_type == FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE:
        entries.append(_entry(
            "Customer credit note",
            [_line(_invoice_revenue_key(operation.invoice), debit=amount), _line("ACCOUNTS_RECEIVABLE", credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Reduce the original revenue category and the customer's open receivable without editing the invoice."
    elif operation_type == FinanceOperation.TYPE_SUPPLIER_BILL:
        entries.append(_entry(
            "Supplier bill",
            [_line(_account_key_from_category(operation), debit=amount), _line("ACCOUNTS_PAYABLE", credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record the approved cost and create an unpaid supplier obligation."
    elif operation_type == FinanceOperation.TYPE_SUPPLIER_PAYMENT:
        entries.append(_entry(
            "Supplier payment",
            [_line("ACCOUNTS_PAYABLE", debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Reduce the selected supplier bill balance and reduce the payment account."
    elif operation_type in {FinanceOperation.TYPE_COMPANY_EXPENSE, FinanceOperation.TYPE_UTILITY_BILL}:
        cost_key = _account_key_from_category(operation)
        payment_status = operation.details.get("payment_status", "UNPAID")
        offset = operation.from_account.gl_account.system_key if payment_status == "PAID" else "ACCOUNTS_PAYABLE"
        entries.append(_entry(
            operation.get_operation_type_display(),
            [_line(cost_key, debit=amount), _line(offset, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record the approved operating cost and either create a payable or reduce the selected payment account."
    elif operation_type == FinanceOperation.TYPE_PAYROLL:
        gross = money(operation.details.get("gross_amount"))
        deductions = money(operation.details.get("deductions"))
        employer = money(operation.details.get("employer_cost"))
        net = money(operation.details.get("net_paid"))
        payroll_type = operation.details.get("payroll_type", "BASE")
        salary_key = "CANADA_SALARIES" if operation.side == "CA" else "BANGLADESH_SALARIES"
        debit_key = {"BASE": salary_key, "COMMISSION": salary_key, "OVERTIME": "OVERTIME", "BONUS": "BONUSES"}.get(
            payroll_type, salary_key
        )
        debit_lines = [_line(debit_key, debit=gross)]
        if employer:
            debit_lines.append(_line("PAYROLL_TAXES", debit=employer))
        credit_lines = [_line("PAYROLL_PAYABLE", credit=net)]
        if deductions + employer:
            credit_lines.append(_line("TAXES_PAYABLE", credit=deductions + employer))
        entries.append(_entry(
            "Payroll accrual", [*debit_lines, *credit_lines], posting_date=operation.details.get("period_end"), currency=operation.currency
        ))
        if operation.from_account_id:
            entries.append(_entry(
                "Payroll payment",
                [_line("PAYROLL_PAYABLE", debit=net), _line(operation.from_account.gl_account.system_key, credit=net)],
                posting_date=operation.transaction_date,
                currency=operation.currency,
            ))
        explanation = "Accrue payroll by department without exposing employee detail; payment separately reduces payroll payable."
    elif operation_type == FinanceOperation.TYPE_PRODUCTION_COST:
        cost_key = PRODUCTION_ACCOUNT_KEYS.get(operation.details.get("cost_category"))
        if not cost_key:
            raise FinanceOperationError("The production cost category is not mapped.")
        offset = operation.from_account.gl_account.system_key if operation.details.get("payment_status") == "PAID" else "ACCOUNTS_PAYABLE"
        entries.append(_entry(
            "Production cost source",
            [_line(cost_key, debit=amount), _line(offset, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record the approved direct production cost through its supplier bill and payment source."
    elif operation_type == FinanceOperation.TYPE_FACTORY_DAILY_COST:
        explanation = "Save or update the costing timeline snapshot. This workflow does not post a General Ledger journal."
    elif operation_type in {
        FinanceOperation.TYPE_BANK_DEPOSIT,
        FinanceOperation.TYPE_BANK_WITHDRAWAL,
        FinanceOperation.TYPE_CASH_DEPOSIT,
        FinanceOperation.TYPE_CASH_WITHDRAWAL,
        FinanceOperation.TYPE_ACCOUNT_TRANSFER,
    }:
        entries = _transfer_preview(operation)
        explanation = (
            "Move the transfer principal between controlled company accounts without recording revenue or expense. "
            "Only a separately entered transfer service fee is recorded as bank-fee expense."
        )
    elif operation_type in {FinanceOperation.TYPE_BANK_FEE, FinanceOperation.TYPE_PROCESSOR_FEE}:
        entries.append(_entry(
            operation.get_operation_type_display(),
            [_line("BANK_FEES", debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record the fee as an operating expense and reduce the selected account."
    elif operation_type == FinanceOperation.TYPE_OWNER_INVESTMENT:
        entries.append(_entry(
            "Owner investment",
            [_line(operation.to_account.gl_account.system_key, debit=amount), _line("OWNER_INVESTMENT", credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Increase company cash or bank and owner equity; this is not revenue."
    elif operation_type == FinanceOperation.TYPE_OWNER_WITHDRAWAL:
        entries.append(_entry(
            "Owner withdrawal",
            [_line("OWNER_WITHDRAWALS", debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record an owner withdrawal against equity; this is not an operating expense."
    elif operation_type == FinanceOperation.TYPE_LOAN_RECEIVED:
        entries.append(_entry(
            "Loan proceeds",
            [_line(operation.to_account.gl_account.system_key, debit=amount), _line("LOANS_PAYABLE", credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Increase company cash or bank and the loan liability; this is not revenue."
    elif operation_type == FinanceOperation.TYPE_LOAN_PRINCIPAL:
        entries.append(_entry(
            "Loan principal payment",
            [_line("LOANS_PAYABLE", debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Reduce the loan principal liability and the payment account."
    elif operation_type == FinanceOperation.TYPE_LOAN_INTEREST:
        entries.append(_entry(
            "Loan interest payment",
            [_line("INTEREST_EXPENSE", debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record only the interest portion as an expense."
    elif operation_type == FinanceOperation.TYPE_SHAREHOLDER_ADVANCE:
        entries.append(_entry(
            "Shareholder advance",
            [_line(operation.to_account.gl_account.system_key, debit=amount), _line("OTHER_LIABILITIES", credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record funds advanced by a shareholder as a liability and increase the selected company account."
    elif operation_type == FinanceOperation.TYPE_SHAREHOLDER_REPAYMENT:
        entries.append(_entry(
            "Shareholder repayment",
            [_line("OTHER_LIABILITIES", debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Reduce the shareholder advance liability when the company repays the shareholder."
    elif operation_type == FinanceOperation.TYPE_ASSET_PURCHASE:
        asset_key = ASSET_ACCOUNT_KEYS.get(operation.details.get("asset_type"), "OTHER_ASSETS")
        entries.append(_entry(
            "Asset purchase",
            [_line(asset_key, debit=amount), _line(operation.from_account.gl_account.system_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Capitalize the approved purchase in the appropriate fixed-asset account instead of normal operating expense."
    elif operation_type == FinanceOperation.TYPE_INVENTORY_ADJUSTMENT:
        adjustment_type = operation.details.get("adjustment_type")
        direction = operation.details.get("direction", "DECREASE")
        if adjustment_type == "OPENING":
            debit_key, credit_key = "INVENTORY", "RETAINED_EARNINGS"
        elif adjustment_type == "PURCHASE":
            debit_key, credit_key = "INVENTORY", "ACCOUNTS_PAYABLE"
        elif direction == "INCREASE":
            debit_key, credit_key = "INVENTORY", "OTHER_INCOME"
        else:
            debit_key, credit_key = "COGS_OTHER_DIRECT", "INVENTORY"
        entries.append(_entry(
            "Inventory adjustment",
            [_line(debit_key, debit=amount), _line(credit_key, credit=amount)],
            posting_date=operation.transaction_date,
            currency=operation.currency,
        ))
        explanation = "Record the evidenced inventory value change through a balanced, traceable adjustment."
    else:
        raise FinanceOperationError("No posting preview is configured for this workflow.")

    keys = {line["account_key"] for entry in entries for line in entry["lines"]}
    names = dict(FinancialAccount.objects.filter(system_key__in=keys).values_list("system_key", "name"))
    missing = sorted(keys - set(names))
    for entry in entries:
        for line in entry["lines"]:
            line["account_name"] = names.get(line["account_key"], "Account not configured")
    return {
        "operation_number": operation.operation_number,
        "operation_type": operation.get_operation_type_display(),
        "posting_date": str(operation.transaction_date),
        "currency": operation.currency,
        "native_amount": str(amount),
        "rate_to_cad": str(operation.rate_to_cad),
        "rate_to_bdt": str(operation.rate_to_bdt),
        "cad_equivalent": str(operation.amount_cad),
        "source_record": operation.operation_number,
        "approval_status": operation.state,
        "simple_explanation": explanation,
        "entries": entries,
        "missing_accounts": missing,
        "can_post": not missing and operation.state in {FinanceOperation.STATE_PENDING, FinanceOperation.STATE_APPROVED},
    }


_DRAFT_INPUT_FIELDS = (
    "operation_type",
    "transaction_date",
    "side",
    "currency",
    "amount_before_tax",
    "tax_amount",
    "total_amount",
    "rate_to_cad",
    "rate_to_bdt",
    "amount_cad",
    "amount_bdt",
    "customer_id",
    "invoice_id",
    "supplier_id",
    "supplier_bill_id",
    "employee_id",
    "department_id",
    "production_order_id",
    "opportunity_id",
    "expense_category_id",
    "from_account_id",
    "to_account_id",
    "reference",
    "payment_method",
    "party_name",
    "business_purpose",
    "reason",
    "notes",
    "details",
    "duplicate_warning",
    "duplicate_warning_text",
    "source_content_type_id",
    "source_object_id",
)


def _check_operation_access(operation, actor):
    from crm.services.financial_permissions import (
        accessible_financial_sides,
        can_submit_finance_operation,
    )

    if not can_submit_finance_operation(actor, operation.operation_type):
        raise FinanceOperationError("You do not have permission to use this Finance workflow.")
    if operation.side not in accessible_financial_sides(actor):
        raise FinanceOperationError("You do not have access to the selected business side.")


def _snapshot_operation_currency(operation, actor):
    snapshot = resolve_currency_snapshot(
        native_amount=operation.total_amount,
        currency=operation.currency,
        transaction_date=operation.transaction_date,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        source_record=None,
        actor=actor,
        create_review=False,
    )
    operation.rate_to_cad = snapshot.rate_to_cad
    operation.rate_to_bdt = snapshot.rate_to_bdt
    operation.amount_cad = snapshot.amount_cad
    operation.amount_bdt = snapshot.amount_bdt


@transaction.atomic
def save_draft_operation(operation, *, actor, existing=None):
    editor = _actor(actor)
    _check_operation_access(operation, editor)
    if existing is not None:
        from crm.services.financial_permissions import can_access_finance_operation, can_manage_financial_transactions

        locked = FinanceOperation.objects.select_for_update().get(pk=existing.pk)
        if locked.state != FinanceOperation.STATE_DRAFT:
            raise FinanceOperationError("Only a draft Finance transaction can be edited.")
        if locked.operation_type != operation.operation_type:
            raise FinanceOperationError("The draft transaction type cannot be changed.")
        if locked.created_by_id != editor.pk and not can_manage_financial_transactions(editor):
            raise FinanceOperationError("You do not have permission to edit this draft.")
        if not can_access_finance_operation(editor, locked):
            raise FinanceOperationError("You do not have access to this draft.")
        before = {"state": locked.state, "amount": str(locked.total_amount)}
        for field_name in _DRAFT_INPUT_FIELDS:
            setattr(locked, field_name, getattr(operation, field_name))
        draft = locked
    else:
        if operation.pk:
            raise FinanceOperationError("This Finance transaction already exists.")
        draft = operation
        draft.operation_number = draft.operation_number or operation_number()
        draft.created_by = editor
        before = None
    draft.risk_level = (
        FinanceOperation.RISK_HIGH
        if draft.operation_type in HIGH_RISK_TYPES
        else FinanceOperation.RISK_STANDARD
    )
    _snapshot_operation_currency(draft, editor)
    draft.state = FinanceOperation.STATE_DRAFT
    draft.modified_by = editor
    draft.submitted_by = None
    draft.submitted_at = None
    draft.posting_preview = {}
    draft.posting_error = ""
    draft.full_clean()
    draft.save()
    audit_operation(
        draft,
        "DRAFT_SAVED",
        editor,
        reason="Finance draft saved.",
        before=before,
        after={"state": draft.state, "type": draft.operation_type, "amount": str(draft.total_amount)},
    )
    return draft


@transaction.atomic
def submit_operation(operation, *, actor):
    submitter = _actor(actor)
    if operation.pk:
        from crm.services.financial_permissions import can_access_finance_operation

        operation = FinanceOperation.objects.select_for_update().get(pk=operation.pk)
        if operation.state != FinanceOperation.STATE_DRAFT:
            raise FinanceOperationError("Only a draft Finance transaction can be submitted.")
        if not can_access_finance_operation(submitter, operation):
            raise FinanceOperationError("You do not have access to this draft.")
    _check_operation_access(operation, submitter)
    operation.operation_number = operation.operation_number or operation_number()
    operation.risk_level = (
        FinanceOperation.RISK_HIGH if operation.operation_type in HIGH_RISK_TYPES else FinanceOperation.RISK_STANDARD
    )
    _snapshot_operation_currency(operation, submitter)
    operation.state = FinanceOperation.STATE_PENDING
    operation.created_by = operation.created_by or submitter
    operation.modified_by = operation.submitted_by = submitter
    operation.submitted_at = timezone.now()
    operation.full_clean()
    operation.save()
    preview = build_posting_preview(operation)
    FinanceOperation.objects.filter(pk=operation.pk).update(posting_preview=preview)
    operation.posting_preview = preview
    audit_operation(
        operation,
        "SUBMITTED",
        submitter,
        reason=operation.business_purpose,
        after={"state": operation.state, "type": operation.operation_type, "amount": str(operation.total_amount)},
    )
    return operation


@transaction.atomic
def review_operation(operation, *, actor, action, notes):
    reviewer = _actor(actor)
    locked = FinanceOperation.objects.select_for_update().get(pk=operation.pk)
    from crm.services.financial_permissions import can_review_finance_operation

    if not can_review_finance_operation(reviewer, locked):
        raise FinanceOperationError("You do not have permission to review this finance operation.")
    if locked.state not in {FinanceOperation.STATE_PENDING, FinanceOperation.STATE_EVIDENCE_REQUIRED}:
        raise FinanceOperationError("Only pending or evidence-required operations can be reviewed.")
    notes = (notes or "").strip()
    if len(notes) < 5:
        raise FinanceOperationError("Review notes must explain the decision.")
    if action == "APPROVE":
        if locked.risk_level == FinanceOperation.RISK_HIGH and locked.submitted_by_id == reviewer.pk:
            raise FinanceOperationError("A user cannot approve their own high-risk finance operation.")
        preview = build_posting_preview(locked)
        if preview["missing_accounts"]:
            raise FinanceOperationError("Posting accounts are incomplete: " + ", ".join(preview["missing_accounts"]))
        next_state = FinanceOperation.STATE_APPROVED
    elif action == "REJECT":
        next_state = FinanceOperation.STATE_REJECTED
    elif action == "EVIDENCE_REQUIRED":
        next_state = FinanceOperation.STATE_EVIDENCE_REQUIRED
    else:
        raise FinanceOperationError("Unknown review action.")
    now = timezone.now()
    update = {
        "state": next_state,
        "change_reason": notes,
        "modified_by": reviewer,
        "modified_at": now,
    }
    if next_state == FinanceOperation.STATE_APPROVED:
        update.update(approved_by=reviewer, approved_at=now, posting_preview=preview)
    FinanceOperation.objects.filter(pk=locked.pk).update(**update)
    locked.refresh_from_db()
    audit_operation(
        locked,
        next_state,
        reviewer,
        reason=notes,
        before={"state": operation.state},
        after={"state": next_state},
    )
    return locked


def _create_generic_journal(operation, *, actor, entry, suffix=""):
    snapshot = _snapshot_from_operation(operation)
    if entry.get("currency") and entry["currency"] != operation.currency:
        destination_amount = money(operation.details["destination_amount"])
        snapshot = resolve_currency_snapshot(
            native_amount=destination_amount,
            currency=entry["currency"],
            transaction_date=operation.transaction_date,
            rate_to_cad=operation.details.get("destination_rate_to_cad"),
            rate_to_bdt=operation.details.get("destination_rate_to_bdt"),
            source_record=operation,
            actor=actor,
        )
    specs = [
        JournalLineSpec(
            line["account_key"],
            debit=_decimal(line["debit"]),
            credit=_decimal(line["credit"]),
            description=entry["description"],
            customer=operation.customer,
            supplier=operation.supplier,
            production_order=operation.production_order,
            department=operation.department,
        )
        for line in entry["lines"]
    ]
    key_suffix = f":{suffix}" if suffix else ""
    return post_journal(
        create_draft_journal(
            journal_date=operation.transaction_date,
            reference=f"FIN-{operation.operation_number}{('-' + suffix) if suffix else ''}",
            description=entry["description"],
            side=entry.get("side") or (operation.side if not suffix else operation.to_account.side),
            snapshot=snapshot,
            source_key=f"FINANCE-OPERATION:{operation.pk}{key_suffix}",
            source_record=operation,
            actor=actor,
            lines=specs,
        ),
        actor=actor,
    )


def _post_customer(operation, actor):
    if operation.operation_type == FinanceOperation.TYPE_CUSTOMER_PAYMENT and operation.invoice_id:
        outstanding = money(operation.invoice.balance)
        if operation.total_amount > outstanding and not operation.details.get("allow_customer_credit"):
            raise FinanceOperationError(
                "The invoice balance changed after submission. Approve a new customer-credit request for any excess."
            )
        applied = min(outstanding, operation.total_amount)
        payment = InvoicePayment(
            payment_date=operation.transaction_date,
            amount=applied,
            currency=operation.currency,
            side=operation.side,
            payment_method=operation.payment_method or "bank",
            rate_to_cad=operation.rate_to_cad,
            rate_to_bdt=operation.rate_to_bdt,
            notes=f"Finance operation {operation.operation_number}; reference {_posting_reference(operation)}",
        )
        recorded = record_invoice_payment(operation.invoice, payment, actor=actor)
        excess = money(operation.total_amount - applied)
        if excess:
            record_customer_receipt(
                customer=operation.customer,
                amount=excess,
                currency=operation.currency,
                receipt_date=operation.transaction_date,
                payment_account=operation.to_account,
                reference=_posting_reference(operation, "-CREDIT"),
                actor=actor,
                invoices=(),
                payment_method=operation.payment_method,
                rate_to_cad=operation.rate_to_cad,
                rate_to_bdt=operation.rate_to_bdt,
                evidence_reference=operation.operation_number,
            )
        journal = getattr(getattr(recorded.payment, "receivable_event", None), "financial_journal", None)
        return recorded.payment, journal
    if operation.operation_type == FinanceOperation.TYPE_CUSTOMER_CREDIT:
        result = record_customer_receipt(
            customer=operation.customer,
            amount=operation.total_amount,
            currency=operation.currency,
            receipt_date=operation.transaction_date,
            payment_account=operation.to_account,
            reference=_posting_reference(operation),
            actor=actor,
            invoices=(),
            payment_method=operation.payment_method,
            rate_to_cad=operation.rate_to_cad,
            rate_to_bdt=operation.rate_to_bdt,
            evidence_reference=operation.operation_number,
        )
        return result["event"], result["journal"]
    if operation.operation_type == FinanceOperation.TYPE_CUSTOMER_REFUND:
        event, _allocation, journal = record_customer_refund(
            customer=operation.customer,
            invoice=operation.invoice,
            amount=operation.total_amount,
            currency=operation.currency,
            refund_date=operation.transaction_date,
            payment_account=operation.from_account,
            reference=_posting_reference(operation),
            evidence_reference=operation.operation_number,
            actor=actor,
            rate_to_cad=operation.rate_to_cad,
            rate_to_bdt=operation.rate_to_bdt,
        )
        return event, journal
    event, _allocation, journal = apply_customer_credit_note(
        invoice=operation.invoice,
        amount=operation.total_amount,
        credit_date=operation.transaction_date,
        reference=_posting_reference(operation),
        evidence_reference=operation.operation_number,
        actor=actor,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
    )
    return event, journal


def _post_supplier_bill(operation, actor):
    category = operation.expense_category
    bill = SupplierBill.objects.create(
        supplier=operation.supplier,
        bill_number=_posting_reference(operation),
        bill_date=operation.transaction_date,
        due_date=date.fromisoformat(operation.details["due_date"]),
        currency=operation.currency,
        amount_before_tax=operation.amount_before_tax,
        tax_amount=operation.tax_amount,
        total_amount=operation.total_amount,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        amount_cad=operation.amount_cad,
        amount_bdt=operation.amount_bdt,
        expense_account=category.default_account,
        production_order=operation.production_order,
        department=operation.department,
        side=operation.side,
        description=operation.business_purpose,
        approval_status=SupplierBill.APPROVAL_PENDING,
        payment_status=SupplierBill.PAYMENT_UNPAID,
        remaining_amount=operation.total_amount,
        created_by=operation.submitted_by,
        modified_by=actor,
        submitted_by=operation.submitted_by,
        submitted_at=operation.submitted_at,
    )
    bill = approve_supplier_bill(bill, actor=actor)
    return bill, bill.payable_journal


def _post_supplier_payment(operation, actor):
    result = record_supplier_payment(
        supplier=operation.supplier,
        bills=(operation.supplier_bill,),
        amount=operation.total_amount,
        currency=operation.currency,
        payment_date=operation.transaction_date,
        payment_account=operation.from_account,
        reference=_posting_reference(operation),
        actor=actor,
        payment_method=operation.payment_method,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        evidence_reference=operation.operation_number,
    )
    return result["event"], result["journal"]


def _post_expense(operation, actor):
    payment_status = operation.details.get("payment_status", ExpenseRecord.PAYMENT_UNPAID)
    expense = ExpenseRecord.objects.create(
        expense_number=operation.operation_number,
        expense_date=operation.transaction_date,
        vendor=operation.supplier,
        vendor_name=operation.party_name,
        category=operation.expense_category,
        department=operation.department,
        side=operation.side,
        currency=operation.currency,
        amount_before_tax=operation.amount_before_tax,
        tax_amount=operation.tax_amount,
        total_amount=operation.total_amount,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        amount_cad=operation.amount_cad,
        amount_bdt=operation.amount_bdt,
        payment_method=operation.payment_method,
        payment_account=operation.from_account if payment_status == ExpenseRecord.PAYMENT_PAID else None,
        payment_status=payment_status,
        due_date=date.fromisoformat(operation.details["due_date"]) if operation.details.get("due_date") else None,
        description=operation.notes or operation.business_purpose or operation.operation_number,
        business_purpose=operation.business_purpose,
        approval_status=ExpenseRecord.APPROVAL_DRAFT,
        is_recurring_instance=bool(operation.details.get("is_recurring")),
        production_order=operation.production_order,
        created_by=operation.submitted_by,
        modified_by=operation.submitted_by,
    )
    expense = submit_expense(expense, actor=operation.submitted_by)
    expense = approve_expense(
        expense, actor=actor, rate_to_cad=operation.rate_to_cad, rate_to_bdt=operation.rate_to_bdt
    )
    return expense, expense.journal


def _post_payroll(operation, actor):
    details = operation.details
    batch = PayrollBatch.objects.create(
        reference=operation.operation_number,
        period_start=date.fromisoformat(details["period_start"]),
        period_end=date.fromisoformat(details["period_end"]),
        side=operation.side,
        currency=operation.currency,
        department=operation.department,
        state=PayrollBatch.STATE_PENDING,
        created_by=operation.submitted_by,
        modified_by=operation.submitted_by,
        submitted_by=operation.submitted_by,
        submitted_at=operation.submitted_at,
    )
    gross = money(details["gross_amount"])
    payroll_type = details.get("payroll_type", "BASE")
    amounts = {"base_salary": Decimal("0"), "overtime": Decimal("0"), "bonus": Decimal("0"), "commission": Decimal("0")}
    amounts[{"BASE": "base_salary", "OVERTIME": "overtime", "BONUS": "bonus", "COMMISSION": "commission"}.get(payroll_type, "base_salary")] = gross
    PayrollLine.objects.create(
        batch=batch,
        employee=operation.employee,
        employee_reference=(
            str(operation.employee_id) if operation.employee_id else f"DEPARTMENT-{operation.department_id}"
        ),
        deductions=money(details["deductions"]),
        employer_cost=money(details["employer_cost"]),
        net_pay=money(details["net_paid"]),
        department=operation.department,
        created_by=operation.submitted_by,
        modified_by=operation.submitted_by,
        **amounts,
    )
    batch = approve_payroll_batch(
        batch, actor=actor, rate_to_cad=operation.rate_to_cad, rate_to_bdt=operation.rate_to_bdt
    )
    if operation.from_account_id:
        batch = pay_payroll_batch(
            batch,
            actor=actor,
            payment_date=operation.transaction_date,
            payment_account=operation.from_account,
            rate_to_cad=operation.rate_to_cad,
            rate_to_bdt=operation.rate_to_bdt,
        )
    return batch, batch.journal


def _post_production_cost(operation, actor):
    account = account_by_key(PRODUCTION_ACCOUNT_KEYS[operation.details["cost_category"]])
    bill = SupplierBill.objects.create(
        supplier=operation.supplier,
        bill_number=operation.reference or operation.operation_number,
        bill_date=operation.transaction_date,
        due_date=operation.transaction_date,
        currency=operation.currency,
        amount_before_tax=operation.total_amount,
        tax_amount=Decimal("0"),
        total_amount=operation.total_amount,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        amount_cad=operation.amount_cad,
        amount_bdt=operation.amount_bdt,
        expense_account=account,
        production_order=operation.production_order,
        department=operation.department,
        side=operation.side,
        description=operation.business_purpose,
        approval_status=SupplierBill.APPROVAL_PENDING,
        payment_status=SupplierBill.PAYMENT_UNPAID,
        remaining_amount=operation.total_amount,
        created_by=operation.submitted_by,
        modified_by=actor,
        submitted_by=operation.submitted_by,
        submitted_at=operation.submitted_at,
    )
    bill = approve_supplier_bill(bill, actor=actor)
    if operation.details.get("payment_status") == "PAID":
        record_supplier_payment(
            supplier=operation.supplier,
            bills=(bill,),
            amount=operation.total_amount,
            currency=operation.currency,
            payment_date=operation.transaction_date,
            payment_account=operation.from_account,
            reference=_posting_reference(operation, "-PAY"),
            actor=actor,
            payment_method=operation.payment_method,
            rate_to_cad=operation.rate_to_cad,
            rate_to_bdt=operation.rate_to_bdt,
            evidence_reference=operation.operation_number,
        )
    cost = ProductionCostRecord.objects.create(
        production_order=operation.production_order,
        customer=operation.production_order.customer,
        opportunity=operation.production_order.opportunity,
        category=operation.details["cost_category"],
        estimated_amount=money(operation.details.get("estimated_amount")),
        actual_amount=operation.total_amount,
        currency=operation.currency,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        supplier=operation.supplier,
        supplier_bill=bill,
        approval_status=SupplierBill.APPROVAL_PENDING,
        payment_status=bill.payment_status,
        cost_date=operation.transaction_date,
        description=operation.business_purpose,
        created_by=operation.submitted_by,
        modified_by=actor,
        submitted_by=operation.submitted_by,
        submitted_at=operation.submitted_at,
    )
    cost = approve_production_cost(cost, actor=actor)
    return cost, bill.payable_journal


def _post_factory_cost(operation, actor):
    details = operation.details
    quick_costing = operation.source_record
    snapshot = getattr(quick_costing, "factory_timeline", None)
    if snapshot and snapshot.locked_at:
        if not details.get("actual_days"):
            raise FactoryTimelineError(
                "The approved factory estimate is locked. Record approved actual production days instead."
            )
    else:
        snapshot = save_estimated_factory_timeline(
            quick_costing,
            estimated_days=int(details["estimated_days"]),
            daily_default=quick_costing_factory_default(details["daily_default_id"]),
            estimated_revenue=details["estimated_revenue"],
            other_estimated_cost=details["other_estimated_cost"],
            actor=actor,
            target_margin_percent=details.get("target_margin_percent"),
            approved_minimum_margin_percent=details.get("approved_minimum_margin_percent"),
            daily_amount_snapshot=details["daily_factory_cost"],
        )
    if details.get("actual_days"):
        snapshot = record_actual_factory_timeline(
            snapshot,
            actual_days=details["actual_days"],
            actual_revenue=details["actual_revenue"],
            other_actual_cost=details["other_actual_cost"],
            actor=actor,
        )
    return snapshot, None


def quick_costing_factory_default(pk):
    from crm.models import FactoryRunningCostDefault

    return FactoryRunningCostDefault.objects.get(pk=pk)


def _record_inventory_movement(operation, *, actor):
    details = operation.details
    item = InventoryItem.objects.select_for_update().get(pk=details["inventory_item_id"], is_active=True)
    quantity = abs(_decimal(details["quantity"]))
    direction = details.get("direction", "DECREASE")
    before_quantity = _decimal(item.quantity)
    before_unit_cost = item.unit_cost
    if direction == "DECREASE" and quantity > before_quantity:
        raise FinanceOperationError("The approved inventory reduction exceeds current stock.")

    after_quantity = before_quantity + quantity if direction == "INCREASE" else before_quantity - quantity
    entered_unit_cost = _decimal(details["unit_cost"])
    if direction == "INCREASE":
        previous_value = before_quantity * (before_unit_cost or Decimal("0"))
        after_unit_cost = ((previous_value + (quantity * entered_unit_cost)) / after_quantity).quantize(
            Decimal("0.0001")
        )
    else:
        after_unit_cost = before_unit_cost

    adjustment_type = details["adjustment_type"]
    update_fields = ["quantity", "updated_at"]
    item.quantity = after_quantity
    if direction == "INCREASE":
        item.unit_cost = after_unit_cost
        update_fields.append("unit_cost")
    if adjustment_type == "WASTE":
        item.waste_quantity = _decimal(item.waste_quantity) + quantity
        update_fields.append("waste_quantity")
    elif adjustment_type == "DAMAGE":
        item.damaged_quantity = _decimal(item.damaged_quantity) + quantity
        update_fields.append("damaged_quantity")
    item.save(update_fields=update_fields)

    movement_type = {
        "PURCHASE": "received",
        "WASTE": "damaged",
        "DAMAGE": "damaged",
        "PRODUCTION_USAGE": "consumed",
    }.get(adjustment_type, "adjusted")
    movement = InventoryMovement.objects.create(
        inventory_item=item,
        movement_type=movement_type,
        quantity=quantity,
        reason=operation.reason,
        created_by=actor,
        notes=f"Approved finance operation {operation.operation_number}; direction {direction}",
    )
    audit_operation(
        operation,
        "INVENTORY_BALANCE_UPDATED",
        actor,
        reason=operation.reason,
        before={"quantity": str(before_quantity), "unit_cost": str(before_unit_cost or "")},
        after={
            "quantity": str(after_quantity),
            "unit_cost": str(item.unit_cost or ""),
            "movement_id": movement.pk,
        },
    )
    return movement


def _post_inventory_purchase(operation, actor):
    bill = SupplierBill.objects.create(
        supplier=operation.supplier,
        bill_number=operation.reference or operation.operation_number,
        bill_date=operation.transaction_date,
        due_date=operation.transaction_date,
        currency=operation.currency,
        amount_before_tax=operation.total_amount,
        tax_amount=Decimal("0"),
        total_amount=operation.total_amount,
        rate_to_cad=operation.rate_to_cad,
        rate_to_bdt=operation.rate_to_bdt,
        amount_cad=operation.amount_cad,
        amount_bdt=operation.amount_bdt,
        expense_account=account_by_key("INVENTORY"),
        department=operation.department,
        side=operation.side,
        description=operation.business_purpose,
        approval_status=SupplierBill.APPROVAL_PENDING,
        payment_status=SupplierBill.PAYMENT_UNPAID,
        remaining_amount=operation.total_amount,
        created_by=operation.submitted_by,
        modified_by=actor,
        submitted_by=operation.submitted_by,
        submitted_at=operation.submitted_at,
    )
    bill = approve_supplier_bill(bill, actor=actor)
    return bill, bill.payable_journal


def _post_inventory_adjustment(operation, actor):
    if operation.details.get("adjustment_type") == "PURCHASE":
        source, journal = _post_inventory_purchase(operation, actor)
    else:
        preview = operation.posting_preview or build_posting_preview(operation)
        journal = _create_generic_journal(operation, actor=actor, entry=preview["entries"][0])
        source = journal
    _record_inventory_movement(operation, actor=actor)
    return source, journal


def _dispatch_post(operation, actor):
    if operation.operation_type in {
        FinanceOperation.TYPE_CUSTOMER_PAYMENT,
        FinanceOperation.TYPE_CUSTOMER_REFUND,
        FinanceOperation.TYPE_CUSTOMER_CREDIT_NOTE,
        FinanceOperation.TYPE_CUSTOMER_CREDIT,
    }:
        return _post_customer(operation, actor)
    if operation.operation_type == FinanceOperation.TYPE_SUPPLIER_BILL:
        return _post_supplier_bill(operation, actor)
    if operation.operation_type == FinanceOperation.TYPE_SUPPLIER_PAYMENT:
        return _post_supplier_payment(operation, actor)
    if operation.operation_type in {FinanceOperation.TYPE_COMPANY_EXPENSE, FinanceOperation.TYPE_UTILITY_BILL}:
        return _post_expense(operation, actor)
    if operation.operation_type == FinanceOperation.TYPE_PAYROLL:
        return _post_payroll(operation, actor)
    if operation.operation_type == FinanceOperation.TYPE_PRODUCTION_COST:
        return _post_production_cost(operation, actor)
    if operation.operation_type == FinanceOperation.TYPE_FACTORY_DAILY_COST:
        return _post_factory_cost(operation, actor)
    if operation.operation_type == FinanceOperation.TYPE_INVENTORY_ADJUSTMENT:
        return _post_inventory_adjustment(operation, actor)
    preview = operation.posting_preview or build_posting_preview(operation)
    journals = [
        _create_generic_journal(operation, actor=actor, entry=entry, suffix=str(index) if index > 1 else "")
        for index, entry in enumerate(preview["entries"], start=1)
    ]
    return journals[0], journals[0]


def _record_post_error(operation, actor, message):
    now = timezone.now()
    FinanceOperation.objects.filter(pk=operation.pk).update(
        posting_attempted_at=now,
        posting_error=str(message),
        modified_by=actor,
        modified_at=now,
    )
    operation.refresh_from_db()
    audit_operation(
        operation,
        "POST_BLOCKED",
        actor,
        reason=str(message),
        before={"state": operation.state},
        after={"state": operation.state, "posting_error": str(message)},
    )


def post_operation(operation, *, actor):
    poster = _actor(actor)
    from crm.services.financial_permissions import can_post_finance_operation

    if not can_post_finance_operation(poster):
        raise FinanceOperationError("You do not have permission to post Finance operations.")
    if operation.state != FinanceOperation.STATE_APPROVED:
        raise FinanceOperationError("Only an independently approved operation can be posted.")
    if not getattr(settings, "FINANCIAL_CORE_WRITES_ENABLED", False):
        message = "Finance posting is disabled. The approved operation remains unposted."
        _record_post_error(operation, poster, message)
        raise FinanceOperationWritesDisabled(message)
    try:
        with transaction.atomic():
            locked = FinanceOperation.objects.select_for_update().select_related(
                "customer", "invoice", "supplier", "supplier_bill", "employee", "department",
                "production_order", "opportunity", "expense_category__default_account",
                "from_account__gl_account", "to_account__gl_account", "source_content_type",
            ).get(pk=operation.pk)
            if locked.state != FinanceOperation.STATE_APPROVED:
                raise FinanceOperationError("The operation is no longer awaiting posting.")
            source, journal = _dispatch_post(locked, poster)
            source_type = ContentType.objects.get_for_model(source, for_concrete_model=False)
            now = timezone.now()
            FinanceOperation.objects.filter(pk=locked.pk).update(
                state=FinanceOperation.STATE_POSTED,
                source_content_type=source_type,
                source_object_id=source.pk,
                primary_journal=journal,
                posted_by=poster,
                posted_at=now,
                posting_attempted_at=now,
                posting_error="",
                modified_by=poster,
                modified_at=now,
            )
            locked.refresh_from_db()
            audit_operation(
                locked,
                "POSTED",
                poster,
                after={"state": locked.state, "source_type": source_type.model, "source_id": source.pk},
            )
            return locked
    except Exception as exc:
        _record_post_error(operation, poster, str(exc))
        raise
