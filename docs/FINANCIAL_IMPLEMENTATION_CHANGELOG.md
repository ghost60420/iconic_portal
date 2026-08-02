# Financial Implementation Changelog

## 2026-08-02 - Finance Operations layer

- Added one permission-scoped Finance Operations Center for daily financial entry, approvals, activity, evidence, and posting previews.
- Added a single `FinanceOperation` orchestration record covering 27 customer, supplier, expense, utility, payroll, production, bank/cash, owner, loan, asset, and inventory workflow types.
- Reused the protected payment, receivable, payable, expense, payroll, production-cost, factory-timeline, currency, and journal services; no alternate accounting engine or report formula was introduced.
- Added independent high-risk approval, separate confirmation screens, immutable approved requests, authorized evidence downloads, and safe blocked posting while Core writes remain disabled.
- Added dated currency snapshots, customer balance revalidation, supplier duplicate controls, factory daily-rate snapshot preservation, and controlled inventory movements with before/after audit values.
- Added the additive `0197_finance_operations_layer` migration. It creates only the orchestration table, constraints, relationships, and indexes; it posts no financial data.
- Added operations documentation, permission matrix, posting reference, focused tests, performance evidence, and desktop/mobile visual verification artifacts.
- Left both Financial Core flags off. No production deployment, historical correction, or production activation occurred.

## 2026-08-02 - Final readiness and exception review

- Added immutable historical exception review records and evidenced adjustment requests.
- Added idempotent copied-database exception loading with stable source keys and IDs.
- Added restricted, paginated exception review, evidence upload, separate confirmation, and proposal approval screens.
- Enforced independent approval for high-risk adjustments; approval does not post.
- Added server-side CA/BD, salesperson, department, supplier, production, payroll, bank, journal, exception, and adjustment object scopes.
- Added authorized invoice, Financial Core evidence, legacy accounting attachment, and accounting-document downloads.
- Restricted financial exports and audit views to authorized, side-scoped users.
- Added permission, exception workflow, activation, and reconciliation documentation.
- Rehearsed 128 exceptions on a copied database without posting corrections or activating either Financial Core flag.

## Scope

This change set builds an additive, feature-flagged Financial Core beneath the existing CRM. It does not deploy, activate reporting, delete history, alter historical values, or post guessed opening balances.

## Implemented

- Added a 90-account Chart of Accounts and 29 configurable expense categories, addressed by stable system keys rather than view-level IDs.
- Added financial periods, balanced journals, journal lines, General Ledger, reversal controls, and immutable audit events.
- Added exact-date currency evidence, immutable transaction snapshots, and a missing-rate review queue for CAD, USD, and BDT.
- Connected optional dual-write hooks to the existing protected invoice approval/void and payment reconciliation services. Both hooks default off.
- Added canonical invoice document, approval, and payment states without replacing legacy invoice fields.
- Added customer receipt allocations, partial/full settlement, customer deposits, credit notes, refunds, and reversals.
- Added suppliers, supplier bills, payable events/allocations, partial/full payments, supplier advances, credits, refunds, and reversals.
- Added expenses, administrator-configured categories, recurring draft generation, private payroll batches/lines, payroll accrual and payment.
- Added production cost attribution and Quick Costing factory daily-rate snapshots with timeline and profit variance status.
- Added named cash/bank accounts, bank statement lines, transaction matching, reconciliation approval, and company transfers that do not create income/expense.
- Added formal General Ledger, Trial Balance, P&L, Balance Sheet, Cash Flow, AR/AP Aging, Budget versus Actual, and executive summary services.
- Added additive preview pages under `/accounting/core/`; existing production financial screens remain unchanged.
- Added the database-copy historical exception planner and generated `HISTORICAL_FINANCIAL_EXCEPTION_REPORT.md` without correction writes.
- Added focused tests for posting, settlement, reports, permissions, immutability, query count, rollback-sensitive services, and responsive route rendering.

## Schema migrations

- `0194_financial_core`: additive Financial Core tables, constraints, foreign keys, and indexes.
- `0195_receivable_financial_journal`: optional protected link from Phase 3B receivable events to Financial Core journals.
- `0196_financial_readiness`: additive immutable exception-review and evidenced adjustment-request tables, constraints, and indexes. It imports and posts no data.
- `0197_finance_operations_layer`: additive daily-workflow staging/orchestration table, constraints, relationships, and indexes. It posts no data and changes no activation flag.

`0193_phase3c_invoice_event_source` belongs to the pre-existing Phase 3C worktree and remains part of the rehearsal sequence.

## Rehearsal result

- Forward migration: passed on two database copies.
- Reverse to `crm 0193`: passed on an empty-core database copy.
- Forward reapply: passed.
- SQLite integrity and foreign-key checks: passed.
- Phase 3C population was idempotent: 51 events and 23 allocations reused, none duplicated.
- Historical exception planner: 128 items, including 35 Critical and 93 High.
- No Financial Core journals were created from uncertain history.

## Not activated

Production dual-write and reporting flags remain off. Historical opening balances, legacy AP classification, inventory, tax, equity, loan, and bank statement reconciliation remain review work, not inferred transactions.
