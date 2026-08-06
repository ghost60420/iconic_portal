import csv
import hashlib
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from crm.models import (
    CashBankAccount,
    ExpenseCategory,
    FinancialAccount,
    FinancialPeriod,
    HistoricalExchangeRate,
    InvoiceSettings,
    Supplier,
)
from crm.services.financial_permissions import can_view_finance_readiness


class FinanceSetupImportError(ValueError):
    pass


SUPPORTED_RECORD_TYPES = (
    "financial_account",
    "cash_bank_account",
    "supplier",
    "expense_category",
    "exchange_rate",
    "financial_period",
    "tax_setting",
)

REQUIRED_FIELDS = {
    "financial_account": "code, name, account_type, normal_balance, system_key",
    "cash_bank_account": "name, kind, side, currency, gl_account_system_key",
    "supplier": "code, name, side, default_currency",
    "expense_category": "code, name, default_account_system_key",
    "exchange_rate": (
        "rate_date, source_currency, target_currency, rate, source_name, "
        "evidence_reference, is_approved"
    ),
    "financial_period": "name, start_date, end_date, side, state",
    "tax_setting": "company_name, default_tax_note",
}


def _text(row, name, *, required=False, upper=False):
    value = (row.get(name) or "").strip()
    if required and not value:
        raise FinanceSetupImportError(f"{name} is required")
    if "\x00" in value:
        raise FinanceSetupImportError(f"{name} contains an invalid character")
    return value.upper() if upper else value


def _boolean(row, name, *, default=False):
    value = (row.get(name) or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "y"}:
        return True
    if value in {"0", "false", "no", "n"}:
        return False
    raise FinanceSetupImportError(f"{name} must be true or false")


def _date(row, name):
    value = _text(row, name, required=True)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FinanceSetupImportError(f"{name} must use YYYY-MM-DD") from exc


def _decimal(row, name, *, positive=False):
    value = _text(row, name, required=True)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise FinanceSetupImportError(f"{name} must be a decimal number") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise FinanceSetupImportError(f"{name} must be greater than zero")
    return number


def _choice(value, choices, name):
    allowed = {choice[0] for choice in choices}
    if value not in allowed:
        raise FinanceSetupImportError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _same(instance, expected):
    for field, value in expected.items():
        current = getattr(instance, field)
        if hasattr(current, "pk"):
            current = current.pk
        if hasattr(value, "pk"):
            value = value.pk
        if current != value:
            return False
    return True


def _save_new(instance, actor, approval_reference, file_hash):
    if hasattr(instance, "created_by"):
        instance.created_by = actor
        instance.modified_by = actor
        instance.approved_by = actor
        instance.approved_at = timezone.now()
        instance.change_reason = f"Approved setup import {approval_reference}; SHA-256 {file_hash}"
    instance.full_clean()
    instance.save()


def _financial_account(row, actor, approval_reference, file_hash):
    system_key = _text(row, "system_key", required=True, upper=True)
    code = _text(row, "code", required=True, upper=True)
    parent_key = _text(row, "parent_system_key", upper=True)
    parent = None
    if parent_key:
        parent = FinancialAccount.objects.filter(system_key=parent_key).first()
        if not parent:
            raise FinanceSetupImportError(
                f"parent_system_key {parent_key} must already exist or appear earlier in the file"
            )
    values = {
        "code": code,
        "name": _text(row, "name", required=True),
        "account_type": _choice(
            _text(row, "account_type", required=True, upper=True),
            FinancialAccount.TYPE_CHOICES,
            "account_type",
        ),
        "subtype": _text(row, "subtype", upper=True),
        "normal_balance": _choice(
            _text(row, "normal_balance", required=True, upper=True),
            FinancialAccount.NORMAL_CHOICES,
            "normal_balance",
        ),
        "parent": parent,
        "is_active": _boolean(row, "is_active", default=True),
        "is_control_account": _boolean(row, "is_control_account"),
        "allow_manual_posting": _boolean(row, "allow_manual_posting", default=True),
        "is_sensitive": _boolean(row, "is_sensitive"),
        "system_key": system_key,
        "description": _text(row, "description"),
    }
    existing_by_key = FinancialAccount.objects.filter(system_key=system_key).first()
    existing_by_code = FinancialAccount.objects.filter(code=code).first()
    if existing_by_key and existing_by_code and existing_by_key.pk != existing_by_code.pk:
        raise FinanceSetupImportError(f"account code {code} and system key {system_key} identify different records")
    existing = existing_by_key or existing_by_code
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError(f"financial account {system_key} conflicts with the existing record")
        return False
    _save_new(FinancialAccount(**values), actor, approval_reference, file_hash)
    return True


def _cash_bank_account(row, actor, approval_reference, file_hash):
    name = _text(row, "name", required=True)
    side = _choice(
        _text(row, "side", required=True, upper=True),
        CashBankAccount._meta.get_field("side").choices,
        "side",
    )
    currency = _choice(
        _text(row, "currency", required=True, upper=True),
        CashBankAccount._meta.get_field("currency").choices,
        "currency",
    )
    gl_key = _text(row, "gl_account_system_key", required=True, upper=True)
    gl_account = FinancialAccount.objects.filter(system_key=gl_key, is_active=True).first()
    if not gl_account:
        raise FinanceSetupImportError(f"gl_account_system_key {gl_key} is not configured")
    values = {
        "name": name,
        "kind": _choice(
            _text(row, "kind", required=True, upper=True), CashBankAccount.KIND_CHOICES, "kind"
        ),
        "side": side,
        "currency": currency,
        "gl_account": gl_account,
        "institution_name": _text(row, "institution_name"),
        "masked_reference": _text(row, "masked_reference"),
        "is_active": _boolean(row, "is_active", default=True),
        "is_sensitive": True,
    }
    existing = CashBankAccount.objects.filter(name=name, side=side, currency=currency).first()
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError(f"cash/bank account {name} conflicts with the existing record")
        return False
    _save_new(CashBankAccount(**values), actor, approval_reference, file_hash)
    return True


def _supplier(row, actor, approval_reference, file_hash):
    code = _text(row, "code", required=True, upper=True)
    values = {
        "code": code,
        "name": _text(row, "name", required=True),
        "side": _choice(
            _text(row, "side", required=True, upper=True), Supplier._meta.get_field("side").choices, "side"
        ),
        "default_currency": _choice(
            _text(row, "default_currency", required=True, upper=True),
            Supplier._meta.get_field("default_currency").choices,
            "default_currency",
        ),
        "contact_name": _text(row, "contact_name"),
        "email": _text(row, "email"),
        "phone": _text(row, "phone"),
        "tax_identifier": _text(row, "tax_identifier"),
        "is_active": _boolean(row, "is_active", default=True),
    }
    existing = Supplier.objects.filter(code=code).first()
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError(f"supplier {code} conflicts with the existing record")
        return False
    _save_new(Supplier(**values), actor, approval_reference, file_hash)
    return True


def _expense_category(row, actor, approval_reference, file_hash):
    code = _text(row, "code", required=True, upper=True)
    account_key = _text(row, "default_account_system_key", required=True, upper=True)
    account = FinancialAccount.objects.filter(system_key=account_key, is_active=True).first()
    if not account:
        raise FinanceSetupImportError(f"default_account_system_key {account_key} is not configured")
    values = {
        "code": code,
        "name": _text(row, "name", required=True),
        "subcategory": _text(row, "subcategory"),
        "default_account": account,
        "is_production_cost": _boolean(row, "is_production_cost"),
        "is_active": _boolean(row, "is_active", default=True),
    }
    existing = ExpenseCategory.objects.filter(code=code).first()
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError(f"expense category {code} conflicts with the existing record")
        return False
    _save_new(ExpenseCategory(**values), actor, approval_reference, file_hash)
    return True


def _exchange_rate(row, actor, approval_reference, file_hash):
    rate_date = _date(row, "rate_date")
    source = _choice(
        _text(row, "source_currency", required=True, upper=True),
        HistoricalExchangeRate._meta.get_field("source_currency").choices,
        "source_currency",
    )
    target = _choice(
        _text(row, "target_currency", required=True, upper=True),
        HistoricalExchangeRate._meta.get_field("target_currency").choices,
        "target_currency",
    )
    if source == target:
        raise FinanceSetupImportError("source_currency and target_currency must differ")
    values = {
        "rate_date": rate_date,
        "source_currency": source,
        "target_currency": target,
        "rate": _decimal(row, "rate", positive=True),
        "source_name": _text(row, "source_name", required=True),
        "evidence_reference": _text(row, "evidence_reference", required=True),
        "is_approved": _boolean(row, "is_approved", default=True),
    }
    if not values["is_approved"]:
        raise FinanceSetupImportError("production exchange rates must be approved")
    existing = HistoricalExchangeRate.objects.filter(
        rate_date=rate_date, source_currency=source, target_currency=target
    ).first()
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError(
                f"exchange rate {rate_date} {source}/{target} conflicts with the existing record"
            )
        return False
    _save_new(HistoricalExchangeRate(**values), actor, approval_reference, file_hash)
    return True


def _financial_period(row, actor, approval_reference, file_hash):
    start_date = _date(row, "start_date")
    end_date = _date(row, "end_date")
    side = _text(row, "side", upper=True)
    if side:
        _choice(side, FinancialPeriod._meta.get_field("side").choices, "side")
    values = {
        "name": _text(row, "name", required=True),
        "start_date": start_date,
        "end_date": end_date,
        "side": side,
        "state": _choice(
            _text(row, "state", required=True, upper=True), FinancialPeriod.STATE_CHOICES, "state"
        ),
    }
    existing = FinancialPeriod.objects.filter(start_date=start_date, end_date=end_date, side=side).first()
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError(
                f"financial period {start_date} to {end_date} {side or 'ALL'} conflicts with the existing record"
            )
        return False
    _save_new(FinancialPeriod(**values), actor, approval_reference, file_hash)
    return True


def _tax_setting(row, actor, approval_reference, file_hash):
    values = {
        "company_name": _text(row, "company_name") or "Iconic Apparel House Inc.",
        "default_tax_note": _text(row, "default_tax_note", required=True),
        "is_active": True,
    }
    existing = InvoiceSettings.objects.filter(is_active=True).first()
    if existing:
        if not _same(existing, values):
            raise FinanceSetupImportError("tax setting conflicts with the active invoice settings record")
        return False
    settings_row = InvoiceSettings(**values, updated_by=actor)
    settings_row.full_clean()
    settings_row.save()
    return True


PROCESSORS = {
    "financial_account": _financial_account,
    "cash_bank_account": _cash_bank_account,
    "supplier": _supplier,
    "expense_category": _expense_category,
    "exchange_rate": _exchange_rate,
    "financial_period": _financial_period,
    "tax_setting": _tax_setting,
}


def _read_rows(uploaded_file):
    content = uploaded_file.read()
    uploaded_file.seek(0)
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FinanceSetupImportError("The setup import must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames or "record_type" not in {name.strip().lower() for name in reader.fieldnames}:
        raise FinanceSetupImportError("The CSV must include a record_type column.")
    rows = []
    for line_number, raw_row in enumerate(reader, start=2):
        if len(rows) >= 2000:
            raise FinanceSetupImportError("The setup import cannot exceed 2,000 records.")
        if None in raw_row and any((value or "").strip() for value in raw_row[None] or ()):
            raise FinanceSetupImportError(f"line {line_number}: the row contains more values than the header")
        row = {key.strip().lower(): value for key, value in raw_row.items() if key is not None}
        if not any((value or "").strip() for value in row.values()):
            continue
        record_type = _text(row, "record_type").lower()
        if record_type not in PROCESSORS:
            raise FinanceSetupImportError(
                f"line {line_number}: record_type must be one of: {', '.join(SUPPORTED_RECORD_TYPES)}"
            )
        rows.append((line_number, record_type, row))
    if not rows:
        raise FinanceSetupImportError("The setup import contains no records.")
    return rows, file_hash


@transaction.atomic
def import_finance_setup_csv(*, uploaded_file, actor, approval_reference, apply=False):
    if not actor or not can_view_finance_readiness(actor):
        raise FinanceSetupImportError("Finance setup imports are restricted to CEO or Super Admin users.")
    approval_reference = (approval_reference or "").strip()
    if len(approval_reference) < 5:
        raise FinanceSetupImportError("An approval reference is required.")
    rows, file_hash = _read_rows(uploaded_file)
    created = {record_type: 0 for record_type in SUPPORTED_RECORD_TYPES}
    unchanged = {record_type: 0 for record_type in SUPPORTED_RECORD_TYPES}
    for line_number, record_type, row in rows:
        try:
            was_created = PROCESSORS[record_type](row, actor, approval_reference, file_hash)
        except (FinanceSetupImportError, ValidationError) as exc:
            raise FinanceSetupImportError(f"line {line_number}: {exc}") from exc
        target = created if was_created else unchanged
        target[record_type] += 1
    if not apply:
        transaction.set_rollback(True)
    return {
        "mode": "applied" if apply else "preview",
        "row_count": len(rows),
        "file_sha256": file_hash,
        "created": {key: value for key, value in created.items() if value},
        "unchanged": {key: value for key, value in unchanged.items() if value},
    }
