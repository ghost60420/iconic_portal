# Iconic CRM Financial Core Architecture

## 1. Purpose

The Financial Core is an additive accounting layer underneath the existing CRM workflow:

`Lead -> Opportunity -> Costing / Quotation -> Approval -> Production -> Invoice -> Payment -> Financial Reporting`

CRM records remain the operational source. Posted Financial Core journals become the accounting source. The design does not delete or rewrite legacy financial history and does not infer missing values.

## 2. Boundary between workflow and accounting

| Event | Operational owner | Accounting result after activation |
| --- | --- | --- |
| Lead created/qualified | `Lead` | None. |
| Opportunity created/staged | `Opportunity` | None; pipeline only. |
| Quick costing drafted/revised | `QuickCosting` | None; estimate only. |
| Quick costing approved | Existing costing workflow | Locks the approved estimate and factory timeline snapshot; still no revenue. |
| Production actual cost approved | `ProductionCostRecord` or approved supplier bill/expense | Posts inventory/COGS or expense and AP/cash according to transaction type. |
| Invoice approved and issued | `Invoice` plus `InvoiceFinancialState` | `Dr Accounts Receivable`, `Cr Revenue` (and tax liability where separately mapped). |
| Customer payment received | `ReceivableEvent` plus allocation | `Dr Cash/Bank`, `Cr Accounts Receivable`; unallocated amount is customer credit/deposit, not revenue. |
| Supplier bill approved | `SupplierBill` plus `PayableEvent` | `Dr Expense/COGS/Asset`, `Cr Accounts Payable`. |
| Supplier payment approved | `PayableEvent` plus allocation | `Dr Accounts Payable`, `Cr Cash/Bank`. |
| Immediate expense approved/paid | `ExpenseRecord` | `Dr Expense`, `Cr Cash/Bank`. |
| Owner investment received | Journal source transaction | `Dr Cash/Bank`, `Cr Owner Investment`. |
| Loan proceeds received | Journal source transaction | `Dr Cash/Bank`, `Cr Loans Payable`. |
| Correction | Reversal, credit, refund, or adjustment | New linked journal; original remains immutable. |

## 3. Accounting components

### Chart of Accounts

`FinancialAccount` stores stable account codes, names, types, normal balance, parent hierarchy, sensitivity, and whether an account is a control account. Code resolves account identity; database IDs are never hard-coded in views or services.

Account classes are Assets, Liabilities, Equity, Revenue, Cost of Goods Sold, Operating Expenses, Other Income, and Other Expenses. The initial account set is maintained by an idempotent bootstrap service and contains every account listed in the master instruction.

### Journal and General Ledger

`JournalEntry` is the immutable posting header. `JournalLine` is the debit/credit detail. A posting service is the only supported route from draft to posted. It validates:

- at least two lines;
- exactly one of debit/credit per line;
- native debits equal native credits;
- CAD debits equal CAD credits;
- BDT debits equal BDT credits when a BDT snapshot is supplied;
- supported currency and positive rates;
- open financial period;
- source idempotency;
- approval identity and date.

Posted journals and lines cannot be edited or deleted through model methods. Corrections create a linked reversal or adjustment journal.

### Financial periods

`FinancialPeriod` owns posting availability by date and business side. Open periods accept approved postings. Closed periods reject normal postings. Locked periods cannot be reopened without the explicit finance control service and an audit reason.

### Receivable ledger

The existing Phase 3B `ReceivableEvent` and `ReceivableAllocation` models are retained. Invoice-issued events establish principal; cash receipts, credits, refunds, adjustments, and reversals change the customer position. Allocations connect posted events to invoices. The AR report is a subledger report and must reconcile to the Accounts Receivable control account.

Canonical invoice states are independent dimensions:

- Document: `DRAFT`, `ISSUED`, `VOIDED`
- Approval: `PENDING`, `APPROVED`, `REJECTED`
- Payment: `UNPAID`, `PARTIAL`, `SETTLED`, `CREDIT`, `REFUNDED`

Legacy invoice status fields remain compatibility projections while usage is audited.

### Payable ledger

`SupplierBill` stores the obligation and evidence. `PayableEvent` stores bill, payment, credit, refund, opening, adjustment, or reversal facts. `PayableAllocation` applies settlement events to bills. Supplier bills are not paid merely because an outbound accounting entry exists.

### Cash and bank

`CashBankAccount` links an approved payment instrument to exactly one GL account and currency. Transfers create one balanced journal between company cash/bank accounts and never affect revenue or expense. `BankReconciliation`, statement lines, and matches preserve statement and reconciliation evidence.

### Expenses, recurring bills, and payroll

`ExpenseCategory` provides administrator-configurable categories linked to GL accounts. `ExpenseRecord` owns submission, approval, payment state, tax, business purpose, evidence, and optional production linkage. A recurring template creates drafts only; manual approval is required before posting or payment.

Payroll uses batch headers and private employee lines. The posted ledger contains department/account totals. Employee-level records are restricted to CEO, Super Admin, authorized Finance, and authorized HR.

### Production costs and factory timeline

`ProductionCostRecord` separates estimate from approved actual and links actual cost to evidence, supplier bill/expense, production order, customer, and opportunity. `QuickCostingTimelineSnapshot` stores the approved daily factory rate, days, timeline cost, estimated/actual profit, variance, and traffic-light status. The saved daily rate never changes when a later default changes.

### Currency

Every transaction preserves native amount/currency and conversion snapshots. `HistoricalExchangeRate` stores evidence-backed dated rates. `CurrencyReviewItem` is the manual queue for a missing or ambiguous rate. No service guesses a historical rate.

Conversion direction is explicit:

`converted amount = native amount * rate_to_target`

Triangulation is allowed only when both dated evidence-backed legs exist and the derived path is stored on the transaction. Reports never translate an old transaction using only the latest rate.

### Audit and documents

`FinancialAuditEvent` records action, reason, before/after values, source, and actor. `FinancialDocument` links supporting evidence to a source record. Protected posted records use `PROTECT`; permanent deletion is not a correction path.

## 4. Canonical quotation lifecycle

`QuickCosting` is the canonical fast quotation and revision/approval workflow used by current production paths. Its latest approved, non-superseded revision is the commercial value source for downstream quick-costing workflows.

`CostingHeader` remains the detailed internal costing source where the detailed workflow is used. It does not silently replace a Quick Costing. An invoice retains its explicit `quick_costing` or `costing_header` link, and downstream services read only that approved linked source.

Rules:

1. An official quick quotation is the approved `QuickCosting` revision with a quotation number.
2. Detailed internal cost belongs to the explicitly linked `CostingHeader` and its cost lines.
3. Values transfer only through existing explicit workflow actions; no report merges both sources for one invoice.
4. Revision roots and superseded status prevent multiple approved revisions from counting.
5. Approval identity/time are required before production or invoice linkage.
6. Revisions create a new record; approved values are not overwritten.
7. Invoice creation stores the approved source relation and invoice amount snapshot.
8. Legacy costing models remain until all callers are audited and reconciled.

## 5. Posting and state services

| Service | Responsibility | Input | Output / dependency |
| --- | --- | --- | --- |
| Chart bootstrap | Idempotently create/update approved account definitions by code | Approved account definition list | Chart records; no transactions |
| Currency snapshot | Resolve exact/native or evidence-backed dated conversion | Currency, date, native amount, supplied/saved rates | Immutable CAD/BDT snapshot or review exception |
| Journal posting | Validate and atomically post a balanced journal | Draft header, lines, approver | Posted journal and ledger lines |
| Journal reversal | Create exact inverse linked journal | Posted journal, reason, approver | Posted reversal; original unchanged |
| Invoice accounting | Issue/void canonical invoice accounting | Approved invoice, actor, rates | Invoice state, receivable event, journal |
| Customer receipt | Record receipt and allocate without double count | Customer, amount, account, optional invoices | Receipt event, allocations, journal, residual credit |
| Supplier bill | Approve obligation and payable principal | Draft bill, actor | Posted payable event and bill journal |
| Supplier payment | Record and allocate settlement | Supplier, amount, account, optional bills | Payment event, allocations, journal, residual supplier credit |
| Expense | Submit, approve, post, and reverse expenses | Expense record, actors | Approved record and bill/immediate-payment journal |
| Recurring expense | Generate due drafts idempotently | Template and through date | Draft expenses only |
| Reporting | Group posted lines/subledgers | Date/as-of filters | P&L, balance sheet, cash flow, trial balance, aging, budget actual |
| Reconciliation | Match bank statement lines to journals | Statement and book records | Approved reconciliation with unexplained difference control |

## 6. Report definitions

### Profit and Loss

`Revenue - Cost of Goods Sold = Gross Profit`

`Gross Profit - Operating Expenses = Operating Profit`

`Operating Profit + Other Income - Other Expense - Interest - Taxes = Net Profit`

Only posted journal lines in the selected effective-date range are used. Native and reporting-currency totals remain traceable to journal lines. Incomplete production cost coverage sets an “estimated/incomplete costs” warning.

### Balance Sheet

Balances posted account activity through an as-of date:

`Assets = Liabilities + Equity`

Current-year earnings come from P&L activity, not an unrelated dashboard formula. A non-zero equation difference is an explicit report error.

### Cash Flow

Cash-account journal movements are grouped as operating, investing, or financing by the offset account classification. Opening cash plus net movement must equal closing cash/bank GL balances. Inter-account transfers contribute zero company-wide net cash flow.

### Trial Balance

Every account shows opening debit/credit, period debit/credit, and closing debit/credit. Period and closing debit totals must equal credit totals.

### Aging

AR aging uses outstanding receivable allocations as of the report date and due-date buckets: current, 1-30, 31-60, 61-90, over 90.

AP operational due views show due today/week/month/overdue. AP accounting aging uses 1-30, 31-60, 61-90, over 90.

### Budget versus Actual

`FinancialBudget` stores monthly account/category values. Actual is posted ledger activity for the mapped account. Variance direction depends on the metric: revenue favorable is actual above budget; cost favorable is actual below budget.

## 7. Dashboard contract

After activation, every widget calls the reporting service and links to its filtered supporting report. Dashboard code does not reimplement formulas.

- Approved Sales: latest approved non-superseded quotation value; pipeline, not revenue.
- Invoiced Revenue: posted issued-invoice revenue.
- Payments Received: posted customer cash receipts.
- Accounts Receivable / Money Waiting: AR subledger outstanding.
- Accounts Payable: AP subledger outstanding.
- Total Costs: posted COGS plus operating/other expenses for the period.
- Gross / Net Profit: P&L results.
- Cash / Bank: linked cash-account GL balances.
- Monthly Cash Movement: cash-flow journal movement.
- Canada/Bangladesh: journal side dimension.
- Top Customers/Suppliers: posted revenue/purchase activity by party.
- Currency Exposure: native open monetary assets/liabilities.
- Financial Pipeline: approved quotation, invoiced, collected, and outstanding stages shown separately.
- Business Health Score: documented composite over collection, liquidity, profitability, overdue AR/AP, and data integrity; unavailable inputs reduce completeness rather than being guessed.
- Action Items: exceptions, missing rates, overdue balances, draft approvals, and reconciliation differences.

Refresh behavior is request-time from the database. There is no background cache or stale materialized value in this phase.

## 8. Permissions

Access is enforced in services as well as views.

- CEO/Super Admin: all Financial Core records and reports.
- Finance/Accounts: invoice/payment/bill/expense/journal/reconciliation/report operations, subject to approval separation.
- Director/Manager: approvals and management reports; sensitive account access requires explicit finance role.
- Sales: quotations, allowed invoice status, and assigned customer scope.
- Production: production cost entry and approved production budget scope.
- HR: payroll entry/details only when explicitly authorized.
- Normal staff: no private salary, owner equity, bank detail, company profit, or other employee commission data.

## 9. Release and activation

The implementation uses additive schema migrations. Historical reconciliation runs separately against a database copy. It never runs as a schema migration.

Feature gates:

- Core write activation is off by default until migration and posting tests pass.
- Core report activation is off by default until historical reconciliation and control-account checks pass.
- Preview pages can read the new core and display “unreconciled” status without replacing legacy production reports.

Safe release order:

1. Back up database and verify restore.
2. Deploy additive schema and code with both gates off.
3. Bootstrap approved Chart of Accounts (no transaction rows).
4. Verify application, migrations, permissions, and legacy regressions.
5. Rehearse historical plans against a fresh production copy; resolve/approve exceptions.
6. Enter approved opening balances through journals.
7. Enable core writes for a controlled period and reconcile dual results.
8. Obtain CEO/Finance activation approval.
9. Enable core reports.

Rollback before posting removes the additive migration after verifying every new table is empty. After any journal is posted, code/feature gates may be rolled back, but schema is retained so financial history is not deleted.

## 10. Completion gates

The Financial Core is not “complete” until:

- all posted debits equal credits;
- AR and AP subledgers equal control accounts;
- trial balance balances;
- balance sheet balances;
- cash flow ending cash equals cash/bank ledger balances;
- P&L is entirely traceable to posted journals;
- historical rates and native currency are preserved;
- every unresolved exception is approved and disclosed;
- every report drill-down reaches supporting transactions;
- permission and N+1 tests pass;
- the existing CRM workflow regression suite passes.
