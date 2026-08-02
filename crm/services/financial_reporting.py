from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import CharField, Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from crm.models import (
    CashBankAccount,
    CurrencyReviewItem,
    FinancialAccount,
    FinancialBudget,
    ExpenseRecord,
    JournalEntry,
    JournalLine,
    PayableAllocation,
    ReceivableAllocation,
    SupplierBill,
    QuickCosting,
    ReceivableEvent,
)
from crm.services.financial_currency import money


ZERO = Decimal("0")
POSTED_STATES = (JournalEntry.STATE_POSTED, JournalEntry.STATE_REVERSED)
MONEY_FIELD = DecimalField(max_digits=20, decimal_places=2)


@dataclass(frozen=True)
class ReportFilters:
    start_date: object = None
    end_date: object = None
    as_of_date: object = None
    side: str = ""
    department_id: int | None = None
    customer_id: int | None = None
    supplier_id: int | None = None
    production_order_id: int | None = None


def _line_scope(filters=None):
    filters = filters or ReportFilters()
    qs = JournalLine.objects.filter(journal__state__in=POSTED_STATES)
    if filters.start_date:
        qs = qs.filter(journal__journal_date__gte=filters.start_date)
    if filters.end_date:
        qs = qs.filter(journal__journal_date__lte=filters.end_date)
    if filters.as_of_date:
        qs = qs.filter(journal__journal_date__lte=filters.as_of_date)
    if filters.side:
        qs = qs.filter(journal__side=filters.side)
    if filters.department_id:
        qs = qs.filter(department_id=filters.department_id)
    if filters.customer_id:
        qs = qs.filter(customer_id=filters.customer_id)
    if filters.supplier_id:
        qs = qs.filter(supplier_id=filters.supplier_id)
    if filters.production_order_id:
        qs = qs.filter(production_order_id=filters.production_order_id)
    return qs


def general_ledger(filters=None, account_id=None):
    qs = _line_scope(filters).select_related(
        "journal", "account", "customer", "supplier", "production_order", "department"
    )
    if account_id:
        qs = qs.filter(account_id=account_id)
    return qs.order_by("journal__journal_date", "journal_id", "line_number")


def trial_balance(*, start_date, end_date, side="", department_id=None):
    base = JournalLine.objects.filter(
        journal__state__in=POSTED_STATES,
        journal__journal_date__lte=end_date,
    )
    if side:
        base = base.filter(journal__side=side)
    if department_id:
        base = base.filter(department_id=department_id)
    rows = list(
        base.values("account_id", "account__code", "account__name", "account__account_type", "account__normal_balance")
        .annotate(
            opening_debit=Coalesce(
                Sum("cad_debit", filter=Q(journal__journal_date__lt=start_date)), Value(ZERO), output_field=MONEY_FIELD
            ),
            opening_credit=Coalesce(
                Sum("cad_credit", filter=Q(journal__journal_date__lt=start_date)), Value(ZERO), output_field=MONEY_FIELD
            ),
            period_debit=Coalesce(
                Sum("cad_debit", filter=Q(journal__journal_date__gte=start_date)), Value(ZERO), output_field=MONEY_FIELD
            ),
            period_credit=Coalesce(
                Sum("cad_credit", filter=Q(journal__journal_date__gte=start_date)), Value(ZERO), output_field=MONEY_FIELD
            ),
        )
        .order_by("account__code")
    )
    totals = defaultdict(lambda: ZERO)
    for row in rows:
        opening = money(row["opening_debit"] - row["opening_credit"])
        period = money(row["period_debit"] - row["period_credit"])
        closing = opening + period
        row["opening_debit_balance"] = max(opening, ZERO)
        row["opening_credit_balance"] = max(-opening, ZERO)
        row["closing_debit"] = max(closing, ZERO)
        row["closing_credit"] = max(-closing, ZERO)
        for key in (
            "opening_debit_balance", "opening_credit_balance", "period_debit", "period_credit", "closing_debit", "closing_credit"
        ):
            totals[key] += row[key]
    totals = {key: money(value) for key, value in totals.items()}
    period_difference = money(totals.get("period_debit", ZERO) - totals.get("period_credit", ZERO))
    closing_difference = money(totals.get("closing_debit", ZERO) - totals.get("closing_credit", ZERO))
    return {
        "rows": rows,
        "totals": totals,
        "period_difference": period_difference,
        "closing_difference": closing_difference,
        "is_balanced": period_difference == ZERO and closing_difference == ZERO,
    }


def profit_and_loss(*, start_date, end_date, side="", department_id=None, customer_id=None, production_order_id=None):
    filters = ReportFilters(
        start_date=start_date,
        end_date=end_date,
        side=side,
        department_id=department_id,
        customer_id=customer_id,
        production_order_id=production_order_id,
    )
    rows = list(
        _line_scope(filters)
        .filter(
            account__account_type__in=(
                FinancialAccount.TYPE_REVENUE,
                FinancialAccount.TYPE_COGS,
                FinancialAccount.TYPE_OPERATING_EXPENSE,
                FinancialAccount.TYPE_OTHER_INCOME,
                FinancialAccount.TYPE_OTHER_EXPENSE,
            )
        )
        .values("account_id", "account__code", "account__name", "account__account_type", "account__subtype")
        .annotate(debit=Sum("cad_debit"), credit=Sum("cad_credit"))
        .order_by("account__code")
    )
    sections = defaultdict(list)
    totals = defaultdict(lambda: ZERO)
    for row in rows:
        account_type = row["account__account_type"]
        amount = money(
            row["credit"] - row["debit"]
            if account_type in (FinancialAccount.TYPE_REVENUE, FinancialAccount.TYPE_OTHER_INCOME)
            else row["debit"] - row["credit"]
        )
        row["amount"] = amount
        sections[account_type].append(row)
        totals[account_type] += amount
    revenue = money(totals[FinancialAccount.TYPE_REVENUE])
    cogs = money(totals[FinancialAccount.TYPE_COGS])
    operating_expense = money(totals[FinancialAccount.TYPE_OPERATING_EXPENSE])
    other_income = money(totals[FinancialAccount.TYPE_OTHER_INCOME])
    other_expense = money(totals[FinancialAccount.TYPE_OTHER_EXPENSE])
    gross_profit = revenue - cogs
    operating_profit = gross_profit - operating_expense
    net_profit = operating_profit + other_income - other_expense
    production_links = _line_scope(filters).filter(account__account_type=FinancialAccount.TYPE_COGS).exclude(
        production_order_id__isnull=True
    ).values("production_order_id").distinct().count()
    unlinked_cogs = _line_scope(filters).filter(
        account__account_type=FinancialAccount.TYPE_COGS, production_order_id__isnull=True
    ).exists()
    return {
        "sections": dict(sections),
        "revenue": money(revenue),
        "cogs": money(cogs),
        "gross_profit": money(gross_profit),
        "operating_expenses": money(operating_expense),
        "operating_profit": money(operating_profit),
        "other_income": money(other_income),
        "other_expenses": money(other_expense),
        "net_profit": money(net_profit),
        "cost_coverage_complete": not unlinked_cogs,
        "production_order_count": production_links,
    }


def profit_and_loss_comparison(*, start_date, end_date, **filters):
    days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    current = profit_and_loss(start_date=start_date, end_date=end_date, **filters)
    previous = profit_and_loss(start_date=previous_start, end_date=previous_end, **filters)
    comparison = []
    for key, label in (
        ("revenue", "Revenue"),
        ("cogs", "Cost of Goods Sold"),
        ("gross_profit", "Gross Profit"),
        ("operating_expenses", "Operating Expenses"),
        ("operating_profit", "Operating Profit"),
        ("net_profit", "Net Profit"),
    ):
        current_value = current[key]
        previous_value = previous[key]
        variance = money(current_value - previous_value)
        percent = None if previous_value == 0 else (variance / abs(previous_value) * Decimal("100")).quantize(Decimal("0.01"))
        comparison.append(
            {"key": key, "label": label, "current": current_value, "previous": previous_value, "variance": variance, "percent": percent}
        )
    return {"current": current, "previous": previous, "comparison": comparison, "previous_start": previous_start, "previous_end": previous_end}


def balance_sheet(*, as_of_date, side=""):
    filters = ReportFilters(as_of_date=as_of_date, side=side)
    rows = list(
        _line_scope(filters)
        .filter(account__account_type__in=(FinancialAccount.TYPE_ASSET, FinancialAccount.TYPE_LIABILITY, FinancialAccount.TYPE_EQUITY))
        .values("account_id", "account__code", "account__name", "account__account_type", "account__subtype")
        .annotate(debit=Sum("cad_debit"), credit=Sum("cad_credit"))
        .order_by("account__code")
    )
    sections = defaultdict(list)
    totals = defaultdict(lambda: ZERO)
    for row in rows:
        account_type = row["account__account_type"]
        amount = money(row["debit"] - row["credit"] if account_type == FinancialAccount.TYPE_ASSET else row["credit"] - row["debit"])
        row["amount"] = amount
        sections[account_type].append(row)
        totals[account_type] += amount
    fiscal_start = as_of_date.replace(month=1, day=1)
    earnings = profit_and_loss(start_date=fiscal_start, end_date=as_of_date, side=side)["net_profit"]
    assets = money(totals[FinancialAccount.TYPE_ASSET])
    liabilities = money(totals[FinancialAccount.TYPE_LIABILITY])
    equity_before_earnings = money(totals[FinancialAccount.TYPE_EQUITY])
    equity = money(equity_before_earnings + earnings)
    difference = money(assets - liabilities - equity)
    return {
        "sections": dict(sections),
        "assets": assets,
        "liabilities": liabilities,
        "equity_before_current_year": equity_before_earnings,
        "current_year_earnings": earnings,
        "equity": equity,
        "difference": difference,
        "is_balanced": difference == ZERO,
    }


def _cash_value_fields(currency):
    if currency == "CAD":
        return "cad_debit", "cad_credit"
    if currency == "BDT":
        return "bdt_debit", "bdt_credit"
    return "native_debit", "native_credit"


def cash_and_bank_balances(*, as_of_date, side=""):
    instruments = list(CashBankAccount.objects.filter(is_active=True).select_related("gl_account"))
    if side:
        instruments = [instrument for instrument in instruments if instrument.side == side]
    account_ids = [instrument.gl_account_id for instrument in instruments]
    totals_by_account = {
        row["account_id"]: row
        for row in JournalLine.objects.filter(
            journal__state__in=POSTED_STATES,
            journal__journal_date__lte=as_of_date,
            account_id__in=account_ids,
        )
        .values("account_id")
        .annotate(
            native_debit=Coalesce(Sum("native_debit"), Value(ZERO), output_field=MONEY_FIELD),
            native_credit=Coalesce(Sum("native_credit"), Value(ZERO), output_field=MONEY_FIELD),
            cad_debit=Coalesce(Sum("cad_debit"), Value(ZERO), output_field=MONEY_FIELD),
            cad_credit=Coalesce(Sum("cad_credit"), Value(ZERO), output_field=MONEY_FIELD),
            bdt_debit=Coalesce(Sum("bdt_debit"), Value(ZERO), output_field=MONEY_FIELD),
            bdt_credit=Coalesce(Sum("bdt_credit"), Value(ZERO), output_field=MONEY_FIELD),
        )
    }
    rows = []
    total_cad = ZERO
    for instrument in instruments:
        totals = totals_by_account.get(instrument.gl_account_id, defaultdict(lambda: ZERO))
        if instrument.currency == "CAD":
            balance = money(totals["cad_debit"] - totals["cad_credit"])
        elif instrument.currency == "BDT":
            balance = money(totals["bdt_debit"] - totals["bdt_credit"])
        else:
            balance = money(totals["native_debit"] - totals["native_credit"])
        cad_balance = money(totals["cad_debit"] - totals["cad_credit"])
        total_cad += cad_balance
        rows.append({"instrument": instrument, "balance": balance, "cad_balance": cad_balance})
    return {"rows": rows, "total_cad": money(total_cad)}


def cash_flow(*, start_date, end_date, side=""):
    cash_account_ids = tuple(CashBankAccount.objects.filter(is_active=True).values_list("gl_account_id", flat=True))
    opening = JournalLine.objects.filter(
        journal__state__in=POSTED_STATES,
        journal__journal_date__lt=start_date,
        account_id__in=cash_account_ids,
    )
    period = JournalLine.objects.filter(
        journal__state__in=POSTED_STATES,
        journal__journal_date__range=(start_date, end_date),
        account_id__in=cash_account_ids,
    )
    if side:
        opening = opening.filter(journal__side=side)
        period = period.filter(journal__side=side)
    opening_totals = opening.aggregate(debit=Sum("cad_debit"), credit=Sum("cad_credit"))
    opening_cash = money((opening_totals["debit"] or ZERO) - (opening_totals["credit"] or ZERO))
    cash_lines = list(period.select_related("journal", "account").order_by("journal__journal_date", "journal_id"))
    journal_ids = {line.journal_id for line in cash_lines}
    offsets = defaultdict(list)
    for line in JournalLine.objects.filter(journal_id__in=journal_ids).exclude(account_id__in=cash_account_ids).select_related("account"):
        offsets[line.journal_id].append(line.account)
    sections = {"OPERATING": ZERO, "INVESTING": ZERO, "FINANCING": ZERO, "TRANSFER": ZERO}
    rows = []
    for line in cash_lines:
        movement = money(line.cad_debit - line.cad_credit)
        accounts = offsets.get(line.journal_id, [])
        if not accounts or any(account.system_key == "FX_CLEARING" for account in accounts):
            classification = "TRANSFER"
        elif any(account.account_type == FinancialAccount.TYPE_ASSET and account.subtype == "FIXED_ASSET" for account in accounts):
            classification = "INVESTING"
        elif any(account.account_type == FinancialAccount.TYPE_EQUITY or account.subtype == "LOAN" for account in accounts):
            classification = "FINANCING"
        else:
            classification = "OPERATING"
        sections[classification] += movement
        rows.append({"line": line, "movement": movement, "classification": classification})
    net_operating = money(sections["OPERATING"])
    net_investing = money(sections["INVESTING"])
    net_financing = money(sections["FINANCING"])
    transfer_movement = money(sections["TRANSFER"])
    net_movement = money(net_operating + net_investing + net_financing + transfer_movement)
    ending_cash = money(opening_cash + net_movement)
    book_ending = cash_and_bank_balances(as_of_date=end_date, side=side)["total_cad"]
    return {
        "rows": rows,
        "opening_cash": opening_cash,
        "net_operating": net_operating,
        "net_investing": net_investing,
        "net_financing": net_financing,
        "transfer_movement": transfer_movement,
        "net_movement": net_movement,
        "ending_cash": ending_cash,
        "book_ending_cash": book_ending,
        "difference": money(ending_cash - book_ending),
        "is_reconciled": money(ending_cash - book_ending) == ZERO,
    }


def _aging_bucket(days):
    if days <= 0:
        return "current"
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "over_90"


def accounts_receivable_aging(*, as_of_date, side=""):
    principals = ReceivableEvent.objects.filter(
        kind=ReceivableEvent.KIND_INVOICE_ISSUED,
        state=ReceivableEvent.STATE_POSTED,
        effective_date__lte=as_of_date,
    ).select_related("source_invoice", "customer")
    if side:
        principals = principals.filter(source_invoice__invoice_region=side)
    invoice_ids = [event.source_invoice_id for event in principals]
    allocated_map = {
        row["invoice_id"]: row["total"] or ZERO
        for row in ReceivableAllocation.objects.filter(
            invoice_id__in=invoice_ids,
            state=ReceivableAllocation.STATE_POSTED,
            allocation_date__lte=as_of_date,
        ).values("invoice_id").annotate(total=Sum("signed_amount"))
    }
    rows = []
    totals = defaultdict(lambda: ZERO)
    for event in principals:
        invoice = event.source_invoice
        outstanding = money(event.native_amount - allocated_map.get(invoice.pk, ZERO))
        if outstanding <= 0:
            continue
        due_date = invoice.due_date or event.effective_date
        days = (as_of_date - due_date).days
        bucket = _aging_bucket(days)
        cad_outstanding = money(outstanding * event.rate_to_cad)
        totals[bucket] += cad_outstanding
        rows.append(
            {
                "invoice": invoice,
                "customer": event.customer,
                "currency": event.currency,
                "outstanding": outstanding,
                "cad_outstanding": cad_outstanding,
                "due_date": due_date,
                "days_overdue": max(days, 0),
                "bucket": bucket,
            }
        )
    return {"rows": rows, "totals": {key: money(value) for key, value in totals.items()}, "total_cad": money(sum(totals.values(), ZERO))}


def accounts_payable_aging(*, as_of_date, side=""):
    bills = SupplierBill.objects.filter(
        approval_status=SupplierBill.APPROVAL_APPROVED,
        bill_date__lte=as_of_date,
    ).select_related("supplier", "department")
    if side:
        bills = bills.filter(side=side)
    allocation_map = {
        row["bill_id"]: row["total"] or ZERO
        for row in PayableAllocation.objects.filter(
            state=PayableAllocation.STATE_POSTED,
            allocation_date__lte=as_of_date,
        ).values("bill_id").annotate(total=Sum("native_amount"))
    }
    rows = []
    totals = defaultdict(lambda: ZERO)
    operational = defaultdict(lambda: ZERO)
    for bill in bills:
        outstanding = money(bill.total_amount - allocation_map.get(bill.pk, ZERO))
        if outstanding <= 0:
            continue
        days = (as_of_date - bill.due_date).days
        bucket = _aging_bucket(days)
        cad_outstanding = money(outstanding * bill.rate_to_cad)
        totals[bucket] += cad_outstanding
        if bill.due_date == as_of_date:
            operational["due_today"] += cad_outstanding
        if as_of_date <= bill.due_date <= as_of_date + timedelta(days=7):
            operational["due_week"] += cad_outstanding
        if as_of_date <= bill.due_date <= as_of_date + timedelta(days=30):
            operational["due_month"] += cad_outstanding
        if bill.due_date < as_of_date:
            operational["overdue"] += cad_outstanding
        rows.append(
            {
                "bill": bill,
                "supplier": bill.supplier,
                "currency": bill.currency,
                "outstanding": outstanding,
                "cad_outstanding": cad_outstanding,
                "due_date": bill.due_date,
                "days_overdue": max(days, 0),
                "bucket": bucket,
            }
        )
    return {
        "rows": rows,
        "totals": {key: money(value) for key, value in totals.items()},
        "operational": {key: money(value) for key, value in operational.items()},
        "total_cad": money(sum(totals.values(), ZERO)),
    }


def budget_vs_actual(*, year, month, side="", department_id=None):
    budgets = FinancialBudget.objects.filter(year=year, month=month).select_related("account", "department")
    if side:
        budgets = budgets.filter(side__in=("", side))
    if department_id:
        budgets = budgets.filter(Q(department_id=department_id) | Q(department_id__isnull=True))
    start_date = date(year, month, 1)
    end_date = date(year, month, calendar_month_days(year, month))
    filters = ReportFilters(start_date=start_date, end_date=end_date, side=side, department_id=department_id)
    actual_map = {
        row["account_id"]: row
        for row in _line_scope(filters).values("account_id").annotate(debit=Sum("cad_debit"), credit=Sum("cad_credit"))
    }
    rows = []
    for budget in budgets:
        actual_row = actual_map.get(budget.account_id, {"debit": ZERO, "credit": ZERO})
        if budget.account.account_type in (FinancialAccount.TYPE_REVENUE, FinancialAccount.TYPE_OTHER_INCOME):
            actual = money((actual_row["credit"] or ZERO) - (actual_row["debit"] or ZERO))
            variance = money(actual - budget.amount)
        else:
            actual = money((actual_row["debit"] or ZERO) - (actual_row["credit"] or ZERO))
            variance = money(budget.amount - actual)
        percent = None if budget.amount == 0 else (variance / abs(budget.amount) * Decimal("100")).quantize(Decimal("0.01"))
        tone = "GREEN" if variance >= 0 else ("YELLOW" if percent is not None and percent >= Decimal("-10") else "RED")
        rows.append({"budget": budget, "actual": actual, "variance": variance, "percent": percent, "status": tone})
    return {"rows": rows, "start_date": start_date, "end_date": end_date}


def calendar_month_days(year, month):
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


def currency_exposure(*, as_of_date):
    ar = accounts_receivable_aging(as_of_date=as_of_date)["rows"]
    ap = accounts_payable_aging(as_of_date=as_of_date)["rows"]
    rows = defaultdict(lambda: {"receivable": ZERO, "payable": ZERO, "net": ZERO})
    for row in ar:
        rows[row["currency"]]["receivable"] += row["outstanding"]
    for row in ap:
        rows[row["currency"]]["payable"] += row["outstanding"]
    for values in rows.values():
        values["net"] = money(values["receivable"] - values["payable"])
    return dict(rows)


def executive_financial_summary(*, start_date, end_date, as_of_date=None, side=""):
    as_of_date = as_of_date or end_date
    period_scope = JournalLine.objects.filter(
        journal__state__in=POSTED_STATES,
        journal__journal_date__range=(start_date, end_date),
    )
    if side:
        period_scope = period_scope.filter(journal__side=side)
    period_rows = list(
        period_scope
        .values(
            "journal__side",
            "account__account_type",
            "account__subtype",
            "account__system_key",
            "customer_id",
            "customer__account_brand",
            "supplier_id",
            "supplier__name",
        )
        .annotate(debit=Sum("cad_debit"), credit=Sum("cad_credit"))
    )
    pnl_totals = defaultdict(lambda: ZERO)
    side_pnl_totals = defaultdict(lambda: defaultdict(lambda: ZERO))
    customer_totals = defaultdict(lambda: ZERO)
    supplier_totals = defaultdict(lambda: ZERO)
    for row in period_rows:
        account_type = row["account__account_type"]
        debit = row["debit"] or ZERO
        credit = row["credit"] or ZERO
        amount = credit - debit if account_type in (FinancialAccount.TYPE_REVENUE, FinancialAccount.TYPE_OTHER_INCOME) else debit - credit
        pnl_totals[account_type] += amount
        side_pnl_totals[row["journal__side"]][account_type] += amount
        if row["customer_id"] and account_type == FinancialAccount.TYPE_REVENUE:
            customer_totals[(row["customer_id"], row["customer__account_brand"] or "Customer")] += amount
        if row["supplier_id"] and account_type in (FinancialAccount.TYPE_COGS, FinancialAccount.TYPE_OPERATING_EXPENSE):
            supplier_totals[(row["supplier_id"], row["supplier__name"] or "Supplier")] += amount

    revenue = money(pnl_totals[FinancialAccount.TYPE_REVENUE])
    cogs = money(pnl_totals[FinancialAccount.TYPE_COGS])
    operating_expenses = money(pnl_totals[FinancialAccount.TYPE_OPERATING_EXPENSE])
    other_income = money(pnl_totals[FinancialAccount.TYPE_OTHER_INCOME])
    other_expense = money(pnl_totals[FinancialAccount.TYPE_OTHER_EXPENSE])
    gross_profit = money(revenue - cogs)
    net_profit = money(gross_profit - operating_expenses + other_income - other_expense)

    invoice_principal_scope = ReceivableEvent.objects.filter(
            kind=ReceivableEvent.KIND_INVOICE_ISSUED,
            state=ReceivableEvent.STATE_POSTED,
            effective_date__lte=as_of_date,
        )
    if side:
        invoice_principal_scope = invoice_principal_scope.filter(source_invoice__invoice_region=side)
    invoice_principals = list(invoice_principal_scope.select_related("source_invoice", "customer"))
    invoice_ids = [event.source_invoice_id for event in invoice_principals]
    ar_allocations = {
        row["invoice_id"]: row["total"] or ZERO
        for row in ReceivableAllocation.objects.filter(
            invoice_id__in=invoice_ids,
            state=ReceivableAllocation.STATE_POSTED,
            allocation_date__lte=as_of_date,
        ).values("invoice_id").annotate(total=Sum("signed_amount"))
    }
    invoiced_revenue = ZERO
    receivables = ZERO
    overdue_ar = ZERO
    ar_by_currency = defaultdict(lambda: ZERO)
    for event in invoice_principals:
        invoice = event.source_invoice
        if start_date <= event.effective_date <= end_date:
            invoiced_revenue += event.amount_cad
        outstanding = max(money(event.native_amount - ar_allocations.get(invoice.pk, ZERO)), ZERO)
        receivables += money(outstanding * event.rate_to_cad)
        ar_by_currency[event.currency] += outstanding
        due = invoice.due_date or event.effective_date
        if due < as_of_date:
            overdue_ar += money(outstanding * event.rate_to_cad)

    bill_scope = SupplierBill.objects.filter(
            approval_status__in=(SupplierBill.APPROVAL_APPROVED, SupplierBill.APPROVAL_PENDING),
            bill_date__lte=as_of_date,
        )
    if side:
        bill_scope = bill_scope.filter(side=side)
    bills = list(bill_scope.select_related("supplier"))
    approved_bill_ids = [bill.pk for bill in bills if bill.approval_status == SupplierBill.APPROVAL_APPROVED]
    ap_allocations = {
        row["bill_id"]: row["total"] or ZERO
        for row in PayableAllocation.objects.filter(
            bill_id__in=approved_bill_ids,
            state=PayableAllocation.STATE_POSTED,
            allocation_date__lte=as_of_date,
        ).values("bill_id").annotate(total=Sum("native_amount"))
    }
    payables = ZERO
    overdue_ap = ZERO
    ap_by_currency = defaultdict(lambda: ZERO)
    pending_bill_count = 0
    for bill in bills:
        if bill.approval_status == SupplierBill.APPROVAL_PENDING:
            pending_bill_count += 1
            continue
        outstanding = max(money(bill.total_amount - ap_allocations.get(bill.pk, ZERO)), ZERO)
        payables += money(outstanding * bill.rate_to_cad)
        ap_by_currency[bill.currency] += outstanding
        if bill.due_date < as_of_date:
            overdue_ap += money(outstanding * bill.rate_to_cad)

    cash_scope = JournalLine.objects.filter(
            journal__state__in=POSTED_STATES,
            journal__journal_date__lte=as_of_date,
            account__cash_instrument__is_active=True,
        )
    if side:
        cash_scope = cash_scope.filter(journal__side=side, account__cash_instrument__side=side)
    cash_rows = list(
        cash_scope
        .annotate(month=TruncMonth("journal__journal_date"))
        .values("month", "account__cash_instrument__kind", "account__cash_instrument__side")
        .annotate(debit=Sum("cad_debit"), credit=Sum("cad_credit"))
        .order_by("month")
    )
    cash_total = ZERO
    cash_in_hand = ZERO
    bank_total = ZERO
    monthly = defaultdict(lambda: ZERO)
    side_cash = defaultdict(lambda: ZERO)
    for row in cash_rows:
        movement = money((row["debit"] or ZERO) - (row["credit"] or ZERO))
        cash_total += movement
        monthly[row["month"]] += movement
        side_cash[row["account__cash_instrument__side"]] += movement
        if row["account__cash_instrument__kind"] != CashBankAccount.KIND_CASH:
            bank_total += movement
        else:
            cash_in_hand += movement

    approved_sales_scope = QuickCosting.objects.filter(
            status__in=QuickCosting.ACTIVE_APPROVED_STATUSES,
            approved_at__date__range=(start_date, end_date),
        )
    if side:
        approved_sales_scope = approved_sales_scope.filter(
            Q(salesperson__access__role=side) | Q(invoices__invoice_region=side)
        ).distinct()
    approved_sales_rows = list(
        approved_sales_scope.values(
            "currency", "quantity", "selling_price_per_piece", "exchange_rate_bdt_per_cad"
        )
    )
    approved_sales_cad = ZERO
    approved_sales_native = defaultdict(lambda: ZERO)
    approved_sales_missing_rate = 0
    for row in approved_sales_rows:
        value = money((row["selling_price_per_piece"] or ZERO) * Decimal(row["quantity"] or 0))
        currency = (row["currency"] or "BDT").upper()
        approved_sales_native[currency] += value
        if currency == "CAD":
            approved_sales_cad += value
        elif currency == "BDT" and row["exchange_rate_bdt_per_cad"] and row["exchange_rate_bdt_per_cad"] > 0:
            approved_sales_cad += money(value / row["exchange_rate_bdt_per_cad"])
        else:
            approved_sales_missing_rate += 1

    receipt_scope = ReceivableEvent.objects.filter(
            kind=ReceivableEvent.KIND_CASH_RECEIPT,
            state=ReceivableEvent.STATE_POSTED,
            effective_date__range=(start_date, end_date),
        )
    if side:
        receipt_scope = receipt_scope.filter(source_invoice__invoice_region=side)
    receipt_rows = list(receipt_scope.values("currency").annotate(native=Sum("native_amount"), cad=Sum("amount_cad")))
    payments_received = money(sum((row["cad"] or ZERO for row in receipt_rows), ZERO))
    payments_by_currency = {row["currency"]: money(row["native"] or ZERO) for row in receipt_rows}

    integrity_rows = CurrencyReviewItem.objects.filter(state=CurrencyReviewItem.STATE_OPEN).order_by().annotate(
        metric=Value("currency", output_field=CharField())
    ).values("metric").annotate(total=Count("id")).union(
        ExpenseRecord.objects.filter(
            approval_status=ExpenseRecord.APPROVAL_PENDING,
            **({"side": side} if side else {}),
        ).order_by().annotate(
            metric=Value("expense", output_field=CharField())
        ).values("metric").annotate(total=Count("id")),
        all=True,
    )
    integrity_counts = {row["metric"]: row["total"] for row in integrity_rows}
    open_currency_reviews = integrity_counts.get("currency", 0)
    pending_expense_count = integrity_counts.get("expense", 0)

    collection_ratio = Decimal("100") if invoiced_revenue == 0 else min(payments_received / invoiced_revenue * Decimal("100"), Decimal("100"))
    net_margin = Decimal("0") if revenue == 0 else net_profit / revenue * Decimal("100")
    liquidity_ratio = Decimal("2") if payables == 0 else max(cash_total, ZERO) / payables
    collection_points = min(collection_ratio / Decimal("100") * Decimal("25"), Decimal("25"))
    profit_points = min(max((net_margin + Decimal("10")) / Decimal("30") * Decimal("25"), ZERO), Decimal("25"))
    liquidity_points = min(liquidity_ratio / Decimal("2") * Decimal("20"), Decimal("20"))
    overdue_base = receivables + payables
    overdue_ratio = ZERO if overdue_base == 0 else (overdue_ar + overdue_ap) / overdue_base
    overdue_points = max(Decimal("15") * (Decimal("1") - min(overdue_ratio, Decimal("1"))), ZERO)
    integrity_penalty = min(Decimal(open_currency_reviews + approved_sales_missing_rate) * Decimal("3"), Decimal("15"))
    integrity_points = Decimal("15") - integrity_penalty
    health_score = int(round(collection_points + profit_points + liquidity_points + overdue_points + integrity_points))

    exposure = {}
    for currency in sorted(set(ar_by_currency) | set(ap_by_currency)):
        exposure[currency] = {
            "receivable": money(ar_by_currency[currency]),
            "payable": money(ap_by_currency[currency]),
            "net": money(ar_by_currency[currency] - ap_by_currency[currency]),
        }
    return {
        "approved_sales_cad": money(approved_sales_cad),
        "approved_sales_native": dict(approved_sales_native),
        "approved_sales_missing_rate": approved_sales_missing_rate,
        "invoiced_revenue": money(invoiced_revenue),
        "payments_received": payments_received,
        "payments_by_currency": payments_by_currency,
        "accounts_receivable": money(receivables),
        "accounts_payable": money(payables),
        "total_costs": money(cogs + operating_expenses + other_expense),
        "gross_profit": gross_profit,
        "operating_expenses": operating_expenses,
        "net_profit": net_profit,
        "cash": money(cash_in_hand),
        "bank_balance": money(bank_total),
        "total_liquid": money(cash_total),
        "monthly_cash_movement": [{"month": month, "amount": money(amount)} for month, amount in sorted(monthly.items())],
        "side_summary": {
            side: {
                "revenue": money(side_pnl_totals[side][FinancialAccount.TYPE_REVENUE]),
                "costs": money(
                    side_pnl_totals[side][FinancialAccount.TYPE_COGS]
                    + side_pnl_totals[side][FinancialAccount.TYPE_OPERATING_EXPENSE]
                    + side_pnl_totals[side][FinancialAccount.TYPE_OTHER_EXPENSE]
                ),
                "net_profit": money(
                    side_pnl_totals[side][FinancialAccount.TYPE_REVENUE]
                    - side_pnl_totals[side][FinancialAccount.TYPE_COGS]
                    - side_pnl_totals[side][FinancialAccount.TYPE_OPERATING_EXPENSE]
                    + side_pnl_totals[side][FinancialAccount.TYPE_OTHER_INCOME]
                    - side_pnl_totals[side][FinancialAccount.TYPE_OTHER_EXPENSE]
                ),
                "cash_and_bank": money(side_cash[side]),
            }
            for side in ("CA", "BD")
        },
        "top_customers": [
            {"id": key[0], "name": key[1], "amount": money(value)}
            for key, value in sorted(customer_totals.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "top_suppliers": [
            {"id": key[0], "name": key[1], "amount": money(value)}
            for key, value in sorted(supplier_totals.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "currency_exposure": exposure,
        "financial_pipeline": {
            "approved_sales": money(approved_sales_cad),
            "invoiced": money(invoiced_revenue),
            "collected": payments_received,
            "outstanding": money(receivables),
        },
        "health_score": health_score,
        "health_components": {
            "collection": collection_points.quantize(Decimal("0.1")),
            "profitability": profit_points.quantize(Decimal("0.1")),
            "liquidity": liquidity_points.quantize(Decimal("0.1")),
            "overdue control": overdue_points.quantize(Decimal("0.1")),
            "data integrity": integrity_points.quantize(Decimal("0.1")),
        },
        "action_items": {
            "missing_currency_rates": open_currency_reviews + approved_sales_missing_rate,
            "overdue_receivables_cad": money(overdue_ar),
            "overdue_payables_cad": money(overdue_ap),
            "pending_supplier_bills": pending_bill_count,
            "pending_expenses": pending_expense_count,
        },
    }
