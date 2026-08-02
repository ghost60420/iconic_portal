# Finance Posting Preview Reference

## Preview contract

`build_posting_preview(operation)` is the single preview calculator. Forms and templates do not calculate accounting entries. The result contains:

- posting date;
- original amount and currency;
- immutable rates to CAD and BDT;
- CAD equivalent;
- source operation number;
- approval state;
- plain-language explanation;
- one or more balanced entry groups;
- account system key and resolved account name;
- missing-account list.

Normal scoped users see the explanation and totals. Finance, Accounts, CEO, and Super Admin roles with sensitive-financial access see debit and credit lines. Bank account identity remains restricted on the operation detail page.

## Account mapping

| Workflow | Debit account | Credit account |
| --- | --- | --- |
| Customer receipt | destination bank/cash | AR and/or Customer Deposits |
| Customer refund | AR or Customer Deposits | refund bank/cash |
| Credit note | invoice revenue account | AR |
| Supplier bill | configured category/COGS | AP |
| Supplier payment | AP | payment bank/cash |
| Paid expense/utility | configured expense | payment bank/cash |
| Unpaid expense/utility | configured expense | AP |
| Payroll accrual | salaries/overtime/bonus and Payroll Taxes | Payroll Payable and Taxes Payable |
| Payroll payment | Payroll Payable | payment bank/cash |
| Production cost | mapped direct COGS | AP or payment bank/cash |
| Same-currency company transfer | destination account | source account |
| Cross-currency transfer out | FX Clearing | source account |
| Cross-currency transfer in | destination account | FX Clearing |
| Bank/processor fee | Bank Fees | charged account |
| Owner investment | bank/cash | Owner Investment |
| Owner withdrawal | Owner Withdrawals | bank/cash |
| Loan proceeds | bank/cash | Loans Payable |
| Loan principal | Loans Payable | bank/cash |
| Loan interest | Interest Expense | bank/cash |
| Shareholder advance | bank/cash | Other Liabilities |
| Shareholder repayment | Other Liabilities | bank/cash |
| Asset purchase | asset-class account | bank/cash |
| Inventory opening | Inventory | Retained Earnings |
| Inventory purchase | Inventory | AP |
| Inventory decrease | Other Direct COGS | Inventory |
| Inventory count increase | Inventory | Other Income |
| Factory daily cost | no GL entry | no GL entry |

## Validation before approval

Approval rebuilds the preview under a database lock. It is rejected when a system key is absent from the Chart of Accounts. The final posting confirmation uses the stored approved preview. The dispatcher and canonical services validate current balances again during the atomic posting transaction.

## Transfers

A same-currency transfer uses one entry. A cross-currency transfer uses two balanced native-currency entries through `FX_CLEARING`. Source and destination CAD equivalents must agree within CAD 0.01; fees are recorded separately. Neither entry uses a revenue or expense account.

## Costing-only exception

Factory Daily Cost has a posting preview explaining that it updates the locked Quick Costing timeline snapshot and creates no journal. This is intentional: it is an internal costing estimate/actual, not a standalone external payable or cash event.

## Disabled flag behavior

With Core writes disabled, the preview remains available but `can_post` does not bypass the server flag. A confirmed posting attempt records an audited error and produces no journal or source transaction.
