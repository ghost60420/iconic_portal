# Financial Activation Checklist

## Current decision

**Do not activate. CEO approval is pending and the evidence/reconciliation gates below are open.**

## Evidence and opening balances

- [ ] 35 Critical exceptions resolved or formally approved with evidence.
- [ ] 93 High exceptions reviewed or safely deferred with documented rationale.
- [ ] Cash opening balance approved and matched to evidence.
- [ ] Bank-account openings approved per named account and statement.
- [ ] Accounts Receivable opening reconciled to customer balances.
- [ ] Accounts Payable opening reconciled to supplier balances.
- [ ] Inventory valuation approved.
- [ ] Fixed-asset register and openings approved.
- [ ] Loan principal openings approved.
- [ ] Tax control balances approved.
- [ ] Owner equity and retained earnings approved.
- [ ] Every opening journal balances.

## Security

- [x] Canada/Bangladesh server-side object scoping implemented.
- [x] Salesperson invoice ownership implemented.
- [x] Department expense scoping implemented.
- [x] Payroll, bank, equity/profit, supplier payment, and correction-tool roles enforced.
- [x] Invoice, evidence, accounting attachment, and accounting-document endpoints re-authorize object access.
- [x] Financial exports restricted and side scoped.
- [x] High-risk adjustments require evidence and independent approval.
- [ ] Production Nginx/media routing verified to deny direct access to protected file prefixes.

## Reconciliation

- [x] Copied-database exception load is idempotent: 128 planned, 128 loaded, 0 duplicate on rerun.
- [ ] Trial Balance includes approved openings and balances.
- [ ] Balance Sheet includes approved openings and satisfies Assets = Liabilities + Equity.
- [ ] Profit and Loss traces to complete posted General Ledger activity.
- [ ] Cash Flow ending cash matches approved cash/bank balances.
- [ ] AR equals the customer open-item ledger.
- [ ] AP equals the supplier open-item ledger.
- [ ] No duplicate revenue, customer payment, or supplier payment.
- [ ] No company transfer is income or expense.
- [ ] Missing currency snapshots are resolved or remain visibly blocked.

## Release controls

- [x] Phase 3A protections retained.
- [x] Write and reporting activation flags remain off.
- [x] Rollback approach tested on a copied database.
- [ ] Verified production backup and restore evidence recorded.
- [ ] Full final regression suite passes on the release commit.
- [ ] CEO signs the exception manifest and activation window.
- [ ] Finance signs opening balances and reconciliation report.

Activation requires every unchecked reconciliation/security item or an explicit documented CEO/Finance exception. Evidence-required amounts may not be converted into opening entries by assumption.
