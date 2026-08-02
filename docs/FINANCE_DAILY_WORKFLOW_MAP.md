# Finance Daily Workflow Map

## Shared source contract

Every request is owned by `FinanceOperation` until posting. It stores the original amount/currency, immutable CAD and BDT equivalents, transaction date, business purpose, party, side, department, payment account, evidence links, submitter, approver, preview, and audit history. Posting then creates or calls the canonical Financial Core source listed below.

| Daily workflow | Canonical posted source | Existing posting service | Debit | Credit |
| --- | --- | --- | --- | --- |
| Customer payment | `InvoicePayment`, `ReceivableEvent`, allocation | protected `record_invoice_payment`; receivable receipt service for approved excess | selected bank/cash | AR; approved excess to Customer Deposits |
| Customer refund | refund `ReceivableEvent` | `record_customer_refund` | AR or Customer Deposits | selected bank/cash |
| Credit note | credit-note `ReceivableEvent` | `apply_customer_credit_note` | original revenue category | AR |
| Unapplied customer credit | receipt `ReceivableEvent` | `record_customer_receipt` | selected bank/cash | Customer Deposits |
| Supplier bill | `SupplierBill`, bill `PayableEvent` | `approve_supplier_bill` | configured expense/COGS | AP |
| Supplier payment | payment `PayableEvent`, allocation | `record_supplier_payment` | AP | selected bank/cash |
| Company expense, paid | `ExpenseRecord` | `submit_expense`, `approve_expense` | category account | selected bank/cash |
| Company expense, unpaid | `ExpenseRecord`, `SupplierBill`, payable event | expense and payable services | category account | AP |
| Utility bill | `ExpenseRecord`, and bill if unpaid | expense service | utility category | bank/cash or AP |
| Payroll | `PayrollBatch`, `PayrollLine` | payroll approve/pay services | salary/overtime/bonus and employer payroll cost | Payroll Payable, Taxes Payable; bank on payment |
| Production cost | `SupplierBill`, `ProductionCostRecord` | payable and production-cost services | direct COGS category | AP; bank on explicit payment |
| Factory daily cost | `QuickCostingTimelineSnapshot` | factory timeline service | no journal | no journal |
| Bank deposit | `JournalEntry` | journal service | destination bank | source company account |
| Bank withdrawal | `JournalEntry` | journal service | destination cash | source bank |
| Cash deposit | `JournalEntry` | journal service | destination cash | source company account |
| Cash withdrawal | `JournalEntry` | journal service | destination company account | source cash |
| Company account transfer | one journal, or two FX-clearing journals | journal and currency services | destination account | source account |
| Bank fee | `JournalEntry` | journal service | Bank Fees | selected bank |
| Payment processor fee | `JournalEntry` | journal service | Bank Fees | selected account |
| Owner investment | `JournalEntry` | journal service | selected bank/cash | Owner Investment |
| Owner withdrawal | `JournalEntry` | journal service | Owner Withdrawals | selected bank/cash |
| Loan received | `JournalEntry` | journal service | selected bank/cash | Loans Payable |
| Loan principal payment | `JournalEntry` | journal service | Loans Payable | selected bank/cash |
| Loan interest payment | `JournalEntry` | journal service | Interest Expense | selected bank/cash |
| Shareholder advance | `JournalEntry` | journal service | selected bank/cash | Other Liabilities |
| Shareholder repayment | `JournalEntry` | journal service | Other Liabilities | selected bank/cash |
| Asset purchase | `JournalEntry` | journal service | Machinery, Equipment, Vehicles, or Other Assets | selected bank/cash |
| Inventory opening | journal plus `InventoryMovement` | journal and controlled inventory operation | Inventory | Retained Earnings |
| Inventory purchase | `SupplierBill`, payable event, `InventoryMovement` | payable and inventory operation | Inventory | AP |
| Inventory decrease | journal plus `InventoryMovement` | journal and controlled inventory operation | Other Direct COGS | Inventory |
| Inventory count increase | journal plus `InventoryMovement` | journal and controlled inventory operation | Inventory | Other Income |

## State transitions

```text
DRAFT -> PENDING -> EVIDENCE_REQUIRED -> PENDING
                 -> REJECTED
                 -> APPROVED -> POSTED
```

The implementation creates new requests directly in `PENDING`. `POSTED` is reachable only through the central dispatcher, inside a row-locked atomic transaction, after independent approval and with `FINANCIAL_CORE_WRITES_ENABLED=True`. Failed posting rolls back the complete source/journal operation.

## Currency rules

- CAD: `rate_to_cad=1`; dated or entered rate to BDT is stored.
- BDT: `rate_to_bdt=1`; dated or entered rate to CAD is stored.
- USD: both CAD and BDT paths must be supported by dated evidence or explicit approved rates.
- The operation snapshot is passed into canonical services; later rate changes do not alter an approved request.
- Cross-currency company transfers retain both original currencies and both dated snapshots.

## Date rules

- Payment/refund date owns cash activity.
- Bill date owns supplier and utility obligations.
- Expense date owns paid/unpaid expense recognition.
- Payroll period end owns accrual; payment date owns cash movement.
- Production cost date owns the supplier obligation.
- Operation date owns owner, loan, bank, asset, and inventory journals.

## Reuse decision

One new page family was required because the existing Core screens are accounting-oriented and guarded by live-write controls. Reusing them would either expose journals to daily employees or require live Core posting. The Operations Center reuses the existing Core services, permissions, components, documents, and account mappings while separating submission/approval from posting.
