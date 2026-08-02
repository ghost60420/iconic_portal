# Financial Formula Reference

All formal statements use posted `JournalLine` values. Report currency is CAD unless the report explicitly displays native currency.

## Conversion

- CAD amount = native amount x `rate_to_cad`
- BDT amount = native amount x `rate_to_bdt`
- Same-currency rate = 1
- A cross-currency company transfer posts through `FX_CLEARING`; any evidenced CAD difference posts to `FX_GAIN` or `FX_LOSS`.

## Receivables

- Issued invoice principal = posted `ReceivableEvent(kind=INVOICE_ISSUED).native_amount`; the linked `InvoiceFinancialState` is the document-state projection
- Invoice open balance = max(issued principal - sum(posted signed allocations), 0)
- Partial receipt allocation = min(unallocated receipt, invoice open balance)
- Customer deposit = receipt amount - amount allocated to invoices
- Credit notes reduce the selected invoice; refunds reverse an allocation and reduce cash.
- AR CAD = sum(invoice open native balance x invoice `rate_to_cad`)

AR Aging buckets are Current for not-yet-due items, then 1-30, 31-60, 61-90, and over 90 days past due.

## Payables

- Bill principal = approved `SupplierBill.total_amount`
- Bill open balance = max(bill principal - sum(posted allocations), 0)
- Supplier advance = supplier payment - allocated bill amount
- AP CAD = sum(bill open native balance x bill `rate_to_cad`)

AP Aging includes operational due-today, due-this-week, due-this-month, and overdue views, with overdue detail split into 1-30, 31-60, 61-90, and over 90 days.

## Profit And Loss

- Revenue = revenue-account credits - debits
- Cost of Goods Sold = COGS-account debits - credits
- Gross Profit = Revenue - Cost of Goods Sold
- Operating Expenses = operating-expense debits - credits
- Operating Profit = Gross Profit - Operating Expenses
- Other Income = other-income credits - debits
- Other Expense = other-expense debits - credits
- Net Profit = Operating Profit + Other Income - Other Expense
- Variance = current period - previous period
- Percentage change = variance / absolute previous period x 100; undefined when previous is zero

Estimated Quick Costing profit is never substituted for ledger profit.

## Balance Sheet

- Assets = debit - credit for asset accounts through as-of date
- Liabilities = credit - debit for liability accounts through as-of date
- Equity = credit - debit for equity accounts plus current-year posted earnings where reported
- Balance difference = Assets - (Liabilities + Equity)
- Balanced = rounded balance difference equals zero

Inventory remains incomplete until an approved opening valuation and ongoing inventory postings exist.

## Cash Flow

- Opening cash = cumulative posted cash/bank debits - credits before period start
- Operating cash = cash movements whose offset is not fixed asset, equity, loan, or FX clearing
- Investing cash = cash movements offset by fixed assets
- Financing cash = cash movements offset by equity or loan principal
- Transfers / FX = internal cash movements or movements offset by FX clearing; not revenue or expense
- Net cash movement = operating + investing + financing + transfers/FX
- Ending cash = opening cash + net cash movement
- Reconciliation difference = calculated ending cash - cash/bank ledger balance at period end

## Trial Balance And General Ledger

- Opening debit/credit derives from the signed pre-period account balance and normal presentation side.
- Period debit and credit are posted line sums.
- Closing balance = opening signed balance + period debit - period credit.
- Total debits must equal total credits in native, CAD, and BDT amounts for each journal.
- Trial Balance is valid only when total closing debit equals total closing credit.

## Factory Timeline

- Estimated Timeline Cost = estimated production days x saved daily factory cost
- Actual Timeline Cost = actual production days x the same saved daily factory cost
- Timeline Variance = actual timeline cost - estimated timeline cost
- Estimated Profit = estimated revenue - other estimated cost - estimated timeline cost
- Actual Profit = actual revenue - other actual cost - actual timeline cost
- Profit Variance = actual profit - estimated profit

Green means on time or early and the target is maintained. Yellow means a small delay or target-margin shortfall above the approved minimum. Red means a major delay, a loss, or margin below the approved minimum. The daily rate snapshot locks when the costing is approved.

## Budget

- Actual follows the selected account's normal signed ledger balance for the month.
- Variance = Actual - Budget
- Variance percentage = Variance / absolute Budget x 100; undefined when Budget is zero
- Status thresholds are calculated by the reporting service and must be interpreted in the context of whether the account is revenue or cost.

## Business Health Score

The score is a derived operating indicator, not an accounting balance:

- Collection: up to 25 points from period receipts / invoiced amount
- Profitability: up to 25 points from net margin, scaled from -10% to 20%
- Liquidity: up to 20 points from non-negative cash and bank / AP, capped at 2.0
- Overdue control: up to 15 points after reducing for overdue AR and AP share
- Data integrity: up to 15 points, reduced by three points per open missing-rate or approved-sales conversion exception

The total is rounded to an integer from 0 to 100. It remains provisional while historical control balances are unreconciled.
