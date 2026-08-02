# Financial Source of Truth

## Status

The Financial Core schema and services are implemented but are not the active production source of truth. Both `FINANCIAL_CORE_WRITES_ENABLED` and `FINANCIAL_CORE_REPORTING_ACTIVE` default to `False`. The legacy reports remain active until the historical exception report is approved and every activation gate in `FINANCIAL_CORE_ARCHITECTURE.md` passes.

## Canonical ownership after activation

| Financial value | Canonical owner | Posting service | Canonical calculation | Report consumers |
| --- | --- | --- | --- | --- |
| Approved sales | `QuickCosting` with approved/confirmed status | Existing costing workflow | Quantity times approved selling price; CAD only when its saved rate supports conversion | Executive pipeline only; never P&L revenue |
| Invoiced revenue | Invoice-issued `ReceivableEvent`, linked `InvoiceFinancialState`, revenue `JournalLine` | `issue_invoice_to_financial_core` | Posted invoice-issued event for the invoiced KPI; posted revenue credits for P&L | General Ledger, P&L, dashboard |
| Payments received | Receipt `ReceivableEvent` and cash/bank `JournalLine` | `record_customer_receipt` | Posted receipt event CAD amount for period | Cash Flow, receipt workflow, dashboard |
| Accounts receivable | Issued invoice principal less posted signed `ReceivableAllocation` | Receivable accounting service | Positive open native balance translated using the immutable invoice snapshot | AR Aging, Balance Sheet, dashboard |
| Customer deposits | `CUSTOMER_DEPOSITS` journal account | `record_customer_receipt` | Unallocated overpayment credit balance | Balance Sheet, General Ledger |
| Supplier obligations | `SupplierBill`, bill `PayableEvent`, AP journal | `approve_supplier_bill` | Approved bill principal | AP Aging, Balance Sheet |
| Supplier payments | Payment `PayableEvent`, `PayableAllocation`, cash/bank journal | `record_supplier_payment` | Posted payment event; allocations reduce only selected bill balances | AP Aging, Cash Flow, General Ledger |
| Accounts payable | Approved bill principal less posted `PayableAllocation` | Payables ledger service | Sum of positive open supplier bill balances, translated at bill snapshots | AP Aging, Balance Sheet, dashboard |
| Operating expense | Expense/Payroll source plus posted expense `JournalLine` | Expense and payroll services | Debit less credit in `OPERATING_EXPENSE` accounts | P&L, Budget versus Actual, dashboard |
| Production cost / COGS | Approved supplier bill or expense journal with production link | Payables/expense service; `ProductionCostRecord` validates attribution | Debit less credit in `COGS` accounts | P&L, production trace, dashboard |
| Factory timeline cost | `QuickCostingTimelineSnapshot` | Factory timeline service | Saved daily rate times estimated or actual production days | Quick Costing detail; estimate only until backed by posted cost |
| Gross profit | Posted ledger | Financial reporting service | Revenue minus COGS | P&L, dashboard |
| Operating profit | Posted ledger | Financial reporting service | Gross profit minus operating expenses | P&L |
| Net profit | Posted ledger | Financial reporting service | Operating profit plus other income minus other expense | P&L, dashboard |
| Cash in hand | Active `CashBankAccount(kind=CASH)` and its GL account | All approved cash posting services | Cumulative CAD debit less credit | Cash Flow, Balance Sheet, dashboard |
| Bank balance | Active non-cash `CashBankAccount` GL accounts | Posting and reconciliation services | Cumulative CAD debit less credit | Reconciliation, Cash Flow, Balance Sheet, dashboard |
| Owner equity | Equity `JournalLine` | Approved journal posting | Credit less debit in equity accounts | Balance Sheet, Trial Balance |
| Loans | `LOANS_PAYABLE` journal lines | Approved journal posting | Credit less debit; principal is financing, interest is expense | Balance Sheet, Cash Flow, P&L for interest |
| Taxes | Tax expense and taxes-payable journal lines | Supplier/expense/payroll or approved journal | Ledger balance by tax account | P&L, Balance Sheet, Cash Flow |
| Inventory | `INVENTORY` journal account | Approved journal posting | Ledger account balance | Balance Sheet |
| Currency exposure | Open AR and AP native balances by original currency | Financial reporting service | Receivable native balance minus payable native balance by currency | Dashboard |
| Budget actual | `FinancialBudget` and posted `JournalLine` | Budget form and reporting service | Ledger actual minus budget for matching account/period | Budget versus Actual |
| Business health score | Derived, never posted | `executive_financial_summary` | Weighted collection, profitability, liquidity, overdue control, and data integrity score | Executive dashboard |

## Currency rules

1. Native amount and original currency are never replaced.
2. Each posted transaction stores `rate_to_cad`, `rate_to_bdt`, CAD amount, and BDT amount.
3. Same-currency rate is exactly one.
4. Explicit evidence-backed rates take precedence; otherwise an approved `HistoricalExchangeRate` for the exact transaction date is required.
5. Missing rates create or retain a manual-review item. The service does not substitute the latest rate.
6. Rate direction is always one unit of source currency multiplied by the rate to obtain target currency.

## Date and status rules

- P&L and Cash Flow use journal date in the selected period.
- Balance Sheet uses posted activity through the as-of date.
- Aging uses the invoice/bill due date and open principal at the as-of date.
- Draft, rejected, and voided documents do not contribute to posted financial reports.
- A posted record is corrected only by credit, refund, adjustment, or reversal.

## Activation gates

The Financial Core can replace legacy reporting only after AR and AP controls reconcile, named bank/cash opening balances reconcile to statements, approved historical currency evidence exists, opening equity/loan/inventory/tax balances are approved, the Balance Sheet balances, Cash Flow ending cash agrees to instruments, and the CEO approves activation. The current database-copy rehearsal does not pass these gates.
