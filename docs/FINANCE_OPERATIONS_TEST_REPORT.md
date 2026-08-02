# Finance Operations Test Report

## Environment

- Repository branch: `codex/financial-foundation-phase3c`
- Repository-pinned runtime: Python 3.12, Django 5.2.8
- Accounting flags during route and approval tests: both disabled
- Posting tests: temporary settings override inside isolated Django test transactions only
- Migration rehearsal database: copied local production-like SQLite database, never the live production database

## Focused coverage

The automated suite covers all dynamic daily workflow routes and the following state/accounting behavior:

- customer partial, full, and explicitly approved overpayment;
- post-submission customer balance drift rejection;
- refund, credit note, and unapplied credit;
- supplier duplicate bill detection and partial/full payment;
- paid expense, unpaid utility, and payroll posting;
- production-cost variance and payable source;
- immutable factory daily-rate snapshot;
- bank/cash, fee, owner, loan, shareholder, asset, and inventory preview balance;
- financing transfers excluded from revenue/operating expense;
- inventory purchase payable plus controlled stock movement;
- independent high-risk approval;
- safe posting rejection while the Core flag is off;
- role submission rules, country separation, payroll privacy, object URL tampering, and document download authorization;
- 50-row pagination and bounded query assertions.

## Final results

### Automated verification

| Suite | Result | Duration |
| --- | ---: | ---: |
| Finance Operations focused suite | 16 passed, 0 failed | 3.536 seconds (final rerun) |
| Combined financial suites | 59 passed, 0 failed | Included in regression evidence |
| Full CRM regression | 846 passed, 0 failed | 371.011 seconds |

`python manage.py check`, `python manage.py makemigrations --check --dry-run`, and Python compilation of the changed modules all passed. Expected timeout/error log messages emitted by mocked resilience tests did not fail the suite.

### Migration and rollback rehearsal

- Applied `0197_finance_operations_layer` to `/tmp/iconic_finance_operations_20260802.sqlite3`.
- Confirmed the new operations table was empty before reversal.
- Reversed to `crm 0196` on `/tmp/iconic_finance_operations_rollback_20260802.sqlite3`, then reapplied `0197` successfully.
- SQLite integrity result: `ok`; foreign-key check returned no violations.
- No production database was used or changed.

### Measured route performance

All measurements used the copied QA database and an authenticated Finance/CEO test user. Times are measured server response milliseconds, not estimates.

| Page | Queries | Cold ms | Warm ms |
| --- | ---: | ---: | ---: |
| Operations Center | 4 | 79.61 | 9.79 |
| Approval Center | 5 | 10.93 | 5.83 |
| Today's Activity | 8 | 24.28 | 14.58 |
| Posting Preview | 3 | 15.02 | 8.84 |
| Customer Payment | 6 | 26.60 | 12.02 |
| Supplier Bill | 8 | 44.27 | 76.37 |
| Supplier Payment | 6 | 14.73 | 12.09 |
| Expense Entry | 9 | 26.99 | 25.69 |
| Utility Bill | 5 | 14.50 | 12.19 |
| Payroll | 6 | 15.16 | 12.62 |
| Production Cost | 6 | 23.97 | 18.76 |
| Factory Daily Cost | 5 | 20.11 | 15.74 |

The Executive Financial Core Dashboard remained at 10 queries: baseline cold/warm was 17.04/9.87 ms and post-change was 19.94/9.37 ms. Pagination and bounded query tests passed with 55 approval records. Query counts do not grow per displayed row, and exchange rates are resolved once per preview/posting operation. The expense form uses 9 queries because it loads the authorized account, category, department, vendor, and production selectors; list/detail/approval/activity surfaces remain within their applicable budgets.

### Visual and security verification

- Captured all 12 requested pages at 1440 px desktop and 390 px mobile widths: 24 full-page screenshots.
- Every page returned HTTP 200 after authorized login; no browser console errors occurred.
- Verified disabled-posting banners and read-only preview behavior with both flags off.
- Automated tests passed for role submission, country separation, payroll/bank privacy, object URL tampering, attachment authorization, independent high-risk approval, and disabled-Core rejection.

Measured data is also stored in `artifacts/finance_operations/performance.json`; the screenshot manifest is `artifacts/finance_operations/screenshots/README.md`.
