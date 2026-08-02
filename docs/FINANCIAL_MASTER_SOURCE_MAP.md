# Iconic CRM Financial Master Source Map

## Scope and status

This document describes the repository at Phase 3C before the Financial Core is activated. It is an implementation source map, not an assertion that existing balances are accounting-complete.

The current production reporting path is still based on `Invoice`, `InvoicePayment`, `AccountingEntry`, costing, and production records. Phase 3B/3C `ReceivableEvent` and `ReceivableAllocation` records are dormant reconciliation infrastructure. They must not replace production reports until historical exceptions are approved and the activation checks in `FINANCIAL_CORE_ARCHITECTURE.md` pass.

Currency conventions:

- Native amount means the amount in the transaction's original `CAD`, `USD`, or `BDT` currency.
- `rate_to_cad` means CAD per one unit of native currency.
- `rate_to_bdt` means BDT per one unit of native currency.
- A native CAD transaction therefore has `rate_to_cad = 1`; a native BDT transaction has `rate_to_bdt = 1`.
- Missing historical rates are exceptions. They are not replaced with a current rate in the Financial Core.

## Current workflow ownership

| Workflow stage | Current record | Current financial effect |
| --- | --- | --- |
| Lead | `Lead` (`crm_lead`) | No recognized revenue, receivable, or cash. |
| Opportunity | `Opportunity` (`crm_opportunity`) | Pipeline only; no accounting entry. |
| Costing / quotation | `QuickCosting`, `CostingHeader` | Approved sales value and estimated cost/margin; no accounting entry. |
| Approval | Quick-costing and invoice approval services | Locks workflow values. Invoice approval currently changes invoice state but does not create a double-entry journal. |
| Production | `ProductionOrder` and related material/lifecycle records | Operational progress and estimated/legacy cost links. Not a complete COGS ledger. |
| Invoice | `Invoice` | Invoiced amount and projected balance. Phase 3C can rehearse an invoice-issued receivable event, but this is not active reporting truth. |
| Payment | `InvoicePayment` plus linked `AccountingEntry` | Customer cash receipt; Phase 3A service writes both atomically and updates the invoice projection. |
| Receivable | `Invoice.total_amount - Invoice.paid_amount` | Current report balance. Phase 3B event/allocation ledger is dormant. |
| Revenue / cost / profit | `AccountingEntry` and report classifiers | Cash-oriented legacy classification, sometimes supplemented by invoices or costings. Not a formal accrual ledger. |
| Executive dashboard | `views_accounting.executive_financial_dashboard` | Aggregates the preceding sources and converts with stored snapshots where present, otherwise legacy current-rate fallbacks. |

## Value register

### Revenue

1. **Business meaning:** Recognized operating income for delivered goods or services.
2. **Current model/table:** Primarily `AccountingEntry` / `crm_accountingentry`; some dashboard cards also use approved `QuickCosting` or `Invoice` values.
3. **Current service:** Report-local helpers in `crm/views_accounting.py`; no single recognition service.
4. **Current consumers:** Profit and Loss, executive dashboard, budget versus actual, KPI scorecard, forecast, production profit.
5. **Currency rules:** Legacy reports prefer stored CAD/BDT values but can use the latest `ExchangeRate` as a fallback.
6. **Date rules:** Usually `AccountingEntry.date`; approved-sales pipeline uses costing approval dates; invoice views use effective invoice date.
7. **Status rules:** Cancelled/draft-like entries are excluded inconsistently by report. Draft quotations must not be revenue.
8. **Current formula:** Generally sum of qualifying `INCOME` entries with `direction=IN`; dashboard “approved sales” is a separate approved-costing value.
9. **Known problems:** Cash receipts and revenue can be conflated; accrual recognition is absent; text-based classifications vary; missing historical rates can be converted with a current rate.
10. **Final source of truth:** Posted `JournalLine` credits to `REVENUE` accounts, net of posted reversal/credit lines, using the journal effective date and immutable conversion snapshot.

### Invoiced revenue

1. **Business meaning:** Face value of valid issued customer invoices, separate from cash collected.
2. **Current model/table:** `Invoice` / `crm_invoice`.
3. **Current service:** `crm.services.invoice_state`; Phase 3C rehearsal uses `crm.services.historical_receivables`.
4. **Current consumers:** Accounts Receivable, executive dashboard, balance sheet support calculations, forecast.
5. **Currency rules:** `Invoice.currency` is preserved; historical conversion is not complete for every invoice.
6. **Date rules:** `Invoice.effective_invoice_date` (`invoice_date`, then created date, then issue date).
7. **Status rules:** Current valid states are mapped by `canonical_document_status` and `canonical_approval_status`; cancelled invoices are excluded.
8. **Current formula:** Sum `Invoice.total_amount` for included invoices.
9. **Known problems:** Legacy status fields overlap; invoice currency snapshots are incomplete; invoice approval does not yet post a journal.
10. **Final source of truth:** Posted invoice journal (`Dr Accounts Receivable`, `Cr Revenue`) and its linked invoice financial state.

### Payments received

1. **Business meaning:** Actual customer receipts, not invoices or deposits promised.
2. **Current model/table:** `InvoicePayment` / `crm_invoicepayment`, with one linked `AccountingEntry` where Phase 3A is used.
3. **Current service:** `crm.services.payment_reconciliation.record_invoice_payment`.
4. **Current consumers:** Invoice detail, payment audit, AR, executive dashboard, cash flow.
5. **Currency rules:** Native currency plus `rate_to_cad`, `rate_to_bdt`, `amount_cad`, and `amount_bdt` snapshots.
6. **Date rules:** `payment_date`.
7. **Status rules:** Completed records are protected; deletion is implemented as an audited reconciliation operation, while refund/credit APIs are not yet implemented.
8. **Current formula:** Sum `InvoicePayment.amount` by currency or stored converted value.
9. **Known problems:** Historical payment/accounting links contain exceptions; customer-level unallocated credits are not active; refund and credit-note operations are placeholders.
10. **Final source of truth:** Posted cash-receipt `ReceivableEvent`, allocations, and balanced receipt journal (`Dr Cash/Bank`, `Cr Accounts Receivable` or customer deposits/credit).

### Accounts receivable

1. **Business meaning:** Unsettled valid customer invoices at an as-of date.
2. **Current model/table:** `Invoice.total_amount` and projected `Invoice.paid_amount` in `crm_invoice`.
3. **Current service:** Projection written by `crm.services.invoice_state.write_payment_projection`; dormant validation in `crm.services.receivables_ledger`.
4. **Current consumers:** AR aging, executive dashboard, balance sheet, KPI scorecard, forecast.
5. **Currency rules:** Native invoice currency; legacy combined reports convert to CAD, sometimes with current rates.
6. **Date rules:** Invoice effective date and due date; aging is based on due date (falling back to issue/effective date in some reports).
7. **Status rules:** Cancelled/voided invoices and non-positive balances excluded.
8. **Current formula:** `max(Invoice.total_amount - Invoice.paid_amount, 0)`.
9. **Known problems:** Projection can disagree with payment history; Phase 3C rehearsal found blocking historical exceptions; no active customer-credit balance.
10. **Final source of truth:** Posted invoice-issued receivable principal less posted receipt/credit allocations plus posted refunds/adjustments, reconciled to the AR control account.

### Accounts payable

1. **Business meaning:** Approved supplier obligations not yet settled.
2. **Current model/table:** No canonical supplier-bill model. The report interprets outbound `AccountingEntry` rows in `crm_accountingentry`.
3. **Current service:** `_ap_*` helpers in `crm/views_accounting.py`.
4. **Current consumers:** Accounts Payable dashboard, executive dashboard, balance sheet, KPI scorecard, forecast.
5. **Currency rules:** Entry currency and stored conversions; legacy fallback may use latest rate.
6. **Date rules:** `AccountingEntry.date` is used as bill/due date when no explicit due date exists.
7. **Status rules:** Text/status heuristics determine paid/open/cancelled.
8. **Current formula:** Sum qualifying outbound entries interpreted as unpaid obligations.
9. **Known problems:** A cash outflow is not a supplier bill; due date, partial payment, allocation, supplier credit, and liability recognition are not canonical.
10. **Final source of truth:** Posted `SupplierBill` payable events less posted supplier payment/credit/refund allocations, reconciled to the AP control account.

### Production cost

1. **Business meaning:** Direct cost attributable to a production order.
2. **Current model/table:** Estimated values in `QuickCosting`, `CostingHeader`, and production material records; actuals may be `AccountingEntry` rows linked to `ProductionOrder`.
3. **Current service:** Costing model properties and report-local production/P&L helpers.
4. **Current consumers:** Costing views, production profit, P&L, executive total costs.
5. **Currency rules:** Quick costing stores BDT-per-CAD for some paths; entry conversion follows legacy rules.
6. **Date rules:** Costing date for estimates and entry date for actuals.
7. **Status rules:** Only latest approved costing revision counts in approved reporting; accounting status filters are text based.
8. **Current formula:** Sum material, production, other, shipping, and detailed per-piece estimates; actual reporting sums classified entries.
9. **Known problems:** Estimate and actual are not one reconciled record; several direct-cost categories are text labels; supplier bill/payment evidence is absent.
10. **Final source of truth:** Approved `ProductionCostRecord` actuals posted to COGS/inventory accounts and linked to supplier bills/expenses; estimates remain planning values.

### Factory overhead

1. **Business meaning:** Factory running cost and indirect production overhead.
2. **Current model/table:** No canonical daily-rate snapshot or overhead allocation model.
3. **Current service:** None.
4. **Current consumers:** Existing reports may include matching accounting descriptions as expenses.
5. **Currency rules:** Not defined consistently.
6. **Date rules:** Entry date when present.
7. **Status rules:** Legacy entry status only.
8. **Current formula:** No canonical formula.
9. **Known problems:** Timeline cost, allocation basis, estimated/actual variance, and locked approved snapshot are missing.
10. **Final source of truth:** `QuickCostingTimelineSnapshot` for approved estimate and actual timeline metrics; posted journal lines to Factory Overhead Allocation/operating accounts for accounting actuals.

### Operating expense

1. **Business meaning:** Non-COGS costs required to run the business.
2. **Current model/table:** `AccountingEntry` / `crm_accountingentry`, plus Bangladesh monthly staff records for payroll workflows.
3. **Current service:** P&L and budget report-local classifiers.
4. **Current consumers:** P&L, executive dashboard, cash flow, budget versus actual, KPI scorecard, forecast.
5. **Currency rules:** Native and stored conversions where available; current-rate fallback remains in legacy reports.
6. **Date rules:** Accounting entry date, not a formal invoice/posting/accrual distinction.
7. **Status rules:** Cancelled and transfer entries excluded; approval semantics vary.
8. **Current formula:** Sum entries classified as expense/COGS by `main_type`, direction, category, and text.
9. **Known problems:** No canonical category registry, bill/paid distinction, due date, recurring template, or complete approval/audit contract.
10. **Final source of truth:** Posted expense or supplier-bill journal lines to `OPERATING_EXPENSE` accounts; expense records are workflow/evidence, ledger is reporting truth.

### Gross profit

1. **Business meaning:** Revenue minus Cost of Goods Sold.
2. **Current model/table:** Derived; estimated invoice margin also uses `Invoice.total_amount - Invoice.total_internal_cost`.
3. **Current service:** P&L/dashboard helpers and invoice properties.
4. **Current consumers:** P&L, executive dashboard, production profit.
5. **Currency rules:** Usually combined in CAD using legacy conversion rules.
6. **Date rules:** Report-selected entry dates; invoice estimate uses invoice values without a recognition period.
7. **Status rules:** Depends on underlying report filters.
8. **Current formula:** Legacy income less entries classified as cost, or invoice amount less limited internal cost.
9. **Known problems:** Incomplete actual COGS makes this estimated; cash income can be substituted for recognized revenue.
10. **Final source of truth:** Posted revenue credits minus posted COGS debits for the selected period; report must mark incomplete cost coverage.

### Operating profit

1. **Business meaning:** Gross profit minus operating expenses.
2. **Current model/table:** Derived from `AccountingEntry` classifications.
3. **Current service:** P&L report helpers.
4. **Current consumers:** P&L and executive dashboard support metrics.
5. **Currency rules:** Legacy CAD conversion.
6. **Date rules:** Entry date range.
7. **Status rules:** Report filters.
8. **Current formula:** Revenue - classified costs - classified operating expenses.
9. **Known problems:** Account categories and accrual timing are not ledger-backed.
10. **Final source of truth:** General Ledger revenue - COGS - operating-expense account activity.

### Net profit

1. **Business meaning:** Operating profit plus other income, less interest, taxes, and other expense.
2. **Current model/table:** Derived from `AccountingEntry` rows.
3. **Current service:** P&L and executive dashboard helpers.
4. **Current consumers:** P&L, executive dashboard, balance sheet earnings, KPI scorecard, forecast.
5. **Currency rules:** Legacy combined-CAD conversion.
6. **Date rules:** Entry date range.
7. **Status rules:** Report-local exclusions.
8. **Current formula:** Commonly legacy revenue minus all classified costs/expenses.
9. **Known problems:** Interest/tax categories are not reliably separated; not traceable to a formal trial balance.
10. **Final source of truth:** Period movement of posted revenue, COGS, operating, other-income, other-expense, interest, and tax accounts.

### Cash

1. **Business meaning:** Physical cash and cash-equivalent account balances.
2. **Current model/table:** No cash-account master. Derived from all qualifying `AccountingEntry` inflows minus outflows.
3. **Current service:** Cash-flow/dashboard helpers.
4. **Current consumers:** Executive dashboard, balance sheet, cash flow, forecast.
5. **Currency rules:** Combined CAD via legacy conversion.
6. **Date rules:** All entries through as-of date.
7. **Status rules:** Transfers and cancelled entries excluded in some reports.
8. **Current formula:** Cumulative `IN - OUT`.
9. **Known problems:** Cash and bank are not separated reliably; opening balances and account reconciliations are absent; transfers can distort account-level balances.
10. **Final source of truth:** Closing posted balance of GL accounts linked to active cash instruments.

### Bank balance

1. **Business meaning:** Book balance for each company bank/payment account.
2. **Current model/table:** Bank details in `InvoiceSettings`; no balance-bearing bank-account model.
3. **Current service:** None; dashboard infers cash movement from entries.
4. **Current consumers:** Executive dashboard and balance sheet estimates.
5. **Currency rules:** Not account-specific.
6. **Date rules:** Entry as-of date.
7. **Status rules:** Legacy entry exclusions.
8. **Current formula:** Not canonical.
9. **Known problems:** No Canadian/BD/PayPal/cash subledgers, masked account permissions, or statement reconciliation.
10. **Final source of truth:** GL account linked to `CashBankAccount`, reconciled by approved bank reconciliation.

### Customer deposits

1. **Business meaning:** Customer cash received before it is applied to an earned receivable; a liability/credit, not revenue.
2. **Current model/table:** Invoice deposit percentages and payments; no separate liability/credit balance.
3. **Current service:** Payment reconciliation only for invoice-bound receipts.
4. **Current consumers:** Invoice presentation; not a complete balance-sheet liability.
5. **Currency rules:** Payment snapshot rules.
6. **Date rules:** Payment date.
7. **Status rules:** Recorded payment status.
8. **Current formula:** No canonical deposit ledger.
9. **Known problems:** Risk of treating deposits as revenue or allocating them twice; overpayments cannot remain as customer credit.
10. **Final source of truth:** Unallocated posted customer receipts in the Customer Deposits/customer-credit control account, with later allocation journaled to AR.

### Supplier payments

1. **Business meaning:** Actual settlement of supplier obligations.
2. **Current model/table:** Outbound `AccountingEntry`; no supplier-payment model/allocation.
3. **Current service:** None.
4. **Current consumers:** AP heuristics, cash flow, executive dashboard.
5. **Currency rules:** Accounting-entry conversion fields.
6. **Date rules:** Entry date.
7. **Status rules:** Text/status heuristics.
8. **Current formula:** Sum selected outbound entries.
9. **Known problems:** Payment cannot be proven against bill, partial settlement, credit, or refund history.
10. **Final source of truth:** Posted payable payment/refund events and allocations plus balanced journals.

### Taxes

1. **Business meaning:** Recoverable tax, tax expense, and taxes payable.
2. **Current model/table:** `Invoice.tax_amount` and text/category-classified `AccountingEntry` rows.
3. **Current service:** Report helpers.
4. **Current consumers:** Invoice, P&L, cash flow, balance sheet estimates.
5. **Currency rules:** Transaction currency; legacy conversion.
6. **Date rules:** Invoice or entry date.
7. **Status rules:** Underlying record status.
8. **Current formula:** No reconciled tax-control formula.
9. **Known problems:** Output/input tax and payable/paid timing are not separated.
10. **Final source of truth:** Posted tax account journal lines, linked to invoice/bill/expense evidence and payments.

### Currency exposure

1. **Business meaning:** Open monetary assets and liabilities by original currency.
2. **Current model/table:** Open invoices, payment totals, and interpreted payable `AccountingEntry` rows.
3. **Current service:** `_exec_currency_exposure_rows` in `crm/views_accounting.py`.
4. **Current consumers:** Executive dashboard.
5. **Currency rules:** Groups native currency; combined amounts may use current rates.
6. **Date rules:** Current open balances.
7. **Status rules:** Report inclusion filters.
8. **Current formula:** Receivables and payables grouped by currency, offset by received amounts in dashboard presentation.
9. **Known problems:** AP is heuristic; missing-rate queue absent; exposure and translated accounting balance are not clearly separated.
10. **Final source of truth:** Native closing balances of monetary GL control/accounts by currency, with snapshot completeness flags.

### Owner equity

1. **Business meaning:** Owner contributions, withdrawals, retained earnings, and current-year earnings.
2. **Current model/table:** Text-classified `AccountingEntry` rows; no equity account master.
3. **Current service:** Balance-sheet helper `_bs_owner_capital`.
4. **Current consumers:** Balance sheet.
5. **Currency rules:** Legacy CAD conversion.
6. **Date rules:** Entries through report date.
7. **Status rules:** Legacy exclusions.
8. **Current formula:** Keyword-derived owner capital plus derived profit.
9. **Known problems:** Financing inflows can be mistaken for income; withdrawals and retained earnings are not formal accounts.
10. **Final source of truth:** Posted equity-account journal balances plus period-closing entries.

### Loans

1. **Business meaning:** Outstanding principal liability; interest is a separate expense.
2. **Current model/table:** Text-classified `AccountingEntry` rows.
3. **Current service:** Cash-flow and balance-sheet keyword helpers.
4. **Current consumers:** Cash flow and balance sheet.
5. **Currency rules:** Legacy entry conversion.
6. **Date rules:** Entry date.
7. **Status rules:** Legacy exclusions.
8. **Current formula:** No canonical principal roll-forward.
9. **Known problems:** Proceeds, principal, and interest are not reliably separated.
10. **Final source of truth:** Posted Loans Payable account balance; cash flow classifies principal financing and interest operating/financing per configured policy.

### Inventory

1. **Business meaning:** Cost of materials/work in progress/finished goods controlled by the company.
2. **Current model/table:** Production material records and an estimated balance-sheet helper.
3. **Current service:** `_bs_inventory_value_cad` in `crm/views_accounting.py`.
4. **Current consumers:** Balance sheet.
5. **Currency rules:** Legacy CAD conversion/estimate.
6. **Date rules:** Current production/material records rather than posting date.
7. **Status rules:** Operational state filters.
8. **Current formula:** Estimated production/material value.
9. **Known problems:** No perpetual inventory journal, receipt/issue valuation, or count adjustment control.
10. **Final source of truth:** Posted inventory account balances supported by future inventory movements; until those movements are complete, inventory is explicitly marked estimated and excluded from “reconciled” readiness.

## Current report consumer register

| Report | Current source | Current definition risk |
| --- | --- | --- |
| Accounts Receivable Aging | Invoice balance projection | Historical projection exceptions; no active event ledger. |
| Accounts Payable | Outbound accounting entries | No supplier-bill/payment subledger. |
| Profit and Loss | Classified accounting entries | Cash/accrual ambiguity and text classification. |
| Balance Sheet | Invoices, entries, production estimates | Does not originate from a trial balance and may not balance. |
| Cash Flow | Direction/classification of entries | No cash-account reconciliation or opening account balances. |
| Budget versus Actual | Settings budget map plus entry classifications | Budget is configuration, not a canonical model. |
| KPI Scorecard | Entry classifications, invoice balances, AP heuristics | Inherits all source limitations. |
| Financial Forecast | Historical entry trend plus open invoice/AP heuristics | Scenario model is operational, not accounting truth. |
| Executive Financial Dashboard | Costing, invoice, payment, entry, and report-local helpers | Multiple definitions and fallback conversions. |
| Production Profit | Production links and accounting entries | Actual cost coverage is incomplete. |

## Activation rule

The final sources listed above become active only after all of the following are true for an approved cutover date:

1. Chart of Accounts and control-account mappings are approved.
2. Historical receivables and payables have opening balances or approved exceptions.
3. Every posted journal is balanced in native currency and CAD.
4. Missing historical rates are either resolved with evidence or visibly quarantined.
5. AR and AP subledgers reconcile to their control accounts.
6. Cash/bank opening balances are approved and reconciled.
7. Trial Balance balances, Balance Sheet balances, and Cash Flow closing cash reconciles.
8. CEO/Finance approve the activation report.

Until then, Financial Core reports are labelled **preview / unreconciled**, and existing reports remain the production view.
