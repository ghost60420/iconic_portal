# Financial Final Reconciliation Report

## Rehearsal boundary

- Database: `/tmp/iconic_financial_final_readiness_20260802.sqlite3`
- Source: copied rehearsal database, not live production
- As-of date: 2026-08-02
- Flags: Financial Core writes off; Financial Core reporting activation off
- Historical source rows changed: none
- Review rows created: 128
- Adjustment/opening journals created: 0

## Exception load

| Risk | Total | Resolved | Approved | Evidence required | Deferred |
| --- | ---: | ---: | ---: | ---: | ---: |
| Critical | 35 | 0 | 0 | 35 | 0 |
| High | 93 | 0 | 0 | 93 | 0 |

Area distribution: Accounts Receivable 37 (28 Critical, 9 High); Accounts Payable 66 High; Currency 18 High; Cash/Bank Opening 3 Critical; Inventory 1 Critical; Owner Equity 1 Critical; Loans 1 Critical; Taxes 1 Critical.

## Ledger rehearsal

The dormant receivable ledger contains 51 events and 23 allocations. The safe population plan scanned 41 invoices and 25 payments and still reports 28 blocking exceptions.

| Currency | Outstanding difference | Payment difference | Accounting difference |
| --- | ---: | ---: | ---: |
| BDT | -414,860.00 | -1,800.00 | -1,800.00 |
| CAD | 4,046.00 | 0.00 | 0.00 |
| USD | 0.00 | 0.00 | 0.00 |

Result: **not reconciled**.

## Formal reports

| Control | Mechanical result | Readiness result |
| --- | --- | --- |
| Trial Balance | Debit/credit difference CAD 0.00 | Not accepted: there are zero journal lines and no approved openings |
| Balance Sheet | Assets 0.00; liabilities 0.00; equity 0.00; difference 0.00 | Not accepted: bank, inventory, equity, loan, tax, asset, AR/AP openings are incomplete |
| Profit and Loss | Revenue/COGS/operating expense/net profit all 0.00 | Not accepted: historical General Ledger activity has not been posted |
| Cash Flow | Ending cash 0.00; difference 0.00 | Not accepted: there are zero canonical cash/bank accounts and no statement openings |
| Accounts Receivable | 13 open rows; CAD-equivalent control not reconciled | Blocking BDT/CAD differences remain |
| Accounts Payable | 0 canonical rows | Not accepted: 66 legacy outbound rows require supplier classification/evidence |
| Currency exposure | BDT 560,074; CAD 21,152.25; USD 350 receivable native exposure | AP exposure and missing snapshots are incomplete |

A zero difference produced by an empty General Ledger is not evidence of correctness. Reports remain preview-only.

## Opening balance status

| Account type | Status |
| --- | --- |
| Cash | Evidence required |
| Bank accounts | Evidence required; 3 aggregate critical exceptions and 0 canonical instruments |
| Accounts Receivable | Evidence required; customer ledger differences remain |
| Accounts Payable | Evidence required; no canonical supplier bills/events |
| Inventory | Evidence required |
| Fixed assets | Evidence required; no approved asset register supplied |
| Loans | Evidence required |
| Taxes | Evidence required |
| Owner equity | Evidence required |
| Retained earnings | Evidence required |

## Decision

The review system is ready for CEO/Finance evidence decisions. The Financial Core is **not ready for activation**. No uncertain value was guessed, posted, overwritten, or deleted.

## Rollback rehearsal

On `/tmp/iconic_financial_readiness_rollback_20260802.sqlite3`, migration `0196` was applied, verified to contain zero review/adjustment rows, rolled back to `0195`, reapplied, and followed by `manage.py check`. Every step passed and both readiness tables remained empty. This proves schema reversibility only before readiness data exists; once reviews or adjustments exist, application rollback with flags off is mandatory.
