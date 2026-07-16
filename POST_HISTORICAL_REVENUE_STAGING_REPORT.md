# Post Historical Revenue Staging Report

Date: 2026-07-15 America/Vancouver

## Status

APPROVED FOR PRODUCTION DEPLOYMENT PREPARATION.

Migration 0185 applied successfully in a local staging rehearsal database, and the focused historical date behavior passed for lists, CEO dashboard invoice revenue, financial reports, production profit report, and exports.

Approved product distinction:

- Main Dashboard remains accounting/cash based and intentionally continues to use `AccountingEntry.date`.
- Historical invoice dating applies to CEO Dashboard, Financial reports, Accounts Receivable, Production Profit reports, invoice exports, revenue reporting, and opportunity reporting.
- The Main Dashboard label was clarified to `Accounting Revenue vs Expenses`.
- The CEO Dashboard label was clarified to `Monthly Invoiced Revenue`.

Production was not touched.

## Staging Database Scope

The local worktree did not contain a current copied production database and no AWS/staging SSH hostname was available in the repo metadata. A local staging rehearsal database was created with `iconic_site.settings_testdb`.

- Staging database: `db_rehearsal.sqlite3`
- Test settings: `iconic_site.settings_testdb`
- Production database used: no
- AWS commands run: no
- Production migration run: no
- Production deploy run: no

The local default `db.sqlite3` was not used and remains a zero-byte placeholder in this worktree.

## Backup

Backup directory:

`backups/historical_revenue_staging_20260715_194245/`

Backup file:

`backups/historical_revenue_staging_20260715_194245/db_rehearsal_before_0185.sqlite3`

Backup size:

2.2 MB

SQLite integrity:

`ok`

## Migration Result

Before applying 0185, the staging rehearsal DB was migrated to:

`crm 0184_quick_costing_recall_workflow`

Applied:

`crm 0185_historical_revenue_dates`

Result:

`OK`

Schema verification:

- `crm_invoice.invoice_date` exists, nullable date field.
- `crm_opportunity.opportunity_date` exists, nullable date field.

Final migration state:

- `0183_opportunity_assigned_to_and_more`: applied
- `0184_quick_costing_recall_workflow`: applied
- `0185_historical_revenue_dates`: applied

## Commands Run

```bash
DJANGO_SECRET_KEY=local-staging-secret python3 manage.py migrate crm 0184 --settings=iconic_site.settings_testdb --noinput
DJANGO_SECRET_KEY=local-staging-secret python3 manage.py migrate crm 0185 --settings=iconic_site.settings_testdb --noinput
DJANGO_SECRET_KEY=local-staging-secret python3 manage.py check --settings=iconic_site.settings_testdb
DJANGO_SECRET_KEY=local-staging-secret python3 manage.py makemigrations --check --dry-run --settings=iconic_site.settings_testdb
DJANGO_SECRET_KEY=local-staging-secret python3 manage.py migrate --settings=iconic_site.settings_testdb --noinput
DJANGO_SECRET_KEY=local-staging-secret python3 manage.py test crm.tests --settings=iconic_site.settings_testdb
```

## Test Results

- `manage.py check`: passed
- `makemigrations --check --dry-run`: passed, no changes detected
- `migrate`: passed
- Full CRM regression: passed
- Test count: 494

Expected mocked tracebacks appeared from existing audit and shipment notification failure-path tests. Final test result was `OK`.

## Staging Test Records

Created in `db_rehearsal.sqlite3` only:

- Customer: `Historical Revenue Stage Customer`
- Opportunity: `OPP-HIST-STAGE-2025`
- Opportunity Date: `2025-03-15`
- Invoice: `INV-HIST-STAGE-2025`
- Invoice Date: `2025-03-20`
- Invoice amount: CAD 2,300.00
- Fallback opportunity: `OPP-HIST-FALLBACK`, blank `opportunity_date`
- Fallback invoice: `INV-HIST-FALLBACK`, blank `invoice_date`

Current staging counts after seed:

- Customers: 1
- Opportunities: 3
- Invoices: 2
- Payments: 0
- Accounting entries: 0
- Production orders: 0

No payment, accounting, or production records were created by the historical date feature.

## Verification Results

### Passed

- Opportunity List returned HTTP 200.
- Opportunity List date filter found `OPP-HIST-STAGE-2025` for March 15, 2025.
- Opportunity List date filter excluded the blank-date fallback opportunity.
- Invoice List returned HTTP 200.
- Invoice List date filter found `INV-HIST-STAGE-2025` for March 20, 2025.
- Invoice List date filter excluded the blank-date fallback invoice.
- Invoice List displayed `Historical Entry`.
- Invoice Detail displayed `Historical Entry`.
- Invoice Detail displayed revenue date.
- CEO Operations Dashboard returned HTTP 200.
- CEO invoice revenue chart placed CAD 2,300.00 in March.
- Accounts Receivable returned HTTP 200.
- Accounts Receivable date filter found the March 20, 2025 invoice.
- Production Profit report returned HTTP 200.
- Production Profit report included the March 20, 2025 invoice.
- Production Profit export row date was `2025-03-20`.
- Invoice PDF returned HTTP 200.
- Invoice PDF contained `Revenue date: 2025-03-20`.
- Production Profit Excel export returned HTTP 200.
- Production Profit Excel export content type was XLSX.
- Admin add-opportunity form displayed `opportunity_date`.
- Sales add-opportunity form did not display `opportunity_date`.
- Sales POST with `opportunity_date=2025-03-15` did not persist that historical date.
- Blank invoice date fallback used `created_at`.
- Blank opportunity date fallback used `created_date`.
- Rendered page duplicate-ID scan passed for:
  - Opportunity List
  - Invoice List
  - Main Dashboard
  - CEO Dashboard
  - Accounts Receivable
  - Production Profit

### Approved Distinction

Main Dashboard invoice revenue placement is intentionally excluded.

Evidence:

```text
Main Dashboard date range: 2025-03-15 to 2025-03-20
opp_period: 1
opp_daily_values: [1, 0, 0, 0, 0, 0]
revenue_daily_values: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
```

Root cause:

The Main Dashboard `revenue_daily_values` series is still built from `AccountingEntry.date`, not invoice revenue. The new historical invoice date logic is working, but this dashboard chart is not an invoice-derived revenue chart.

Product decision:

This is approved behavior. Main Dashboard should stay accounting/cash based. No code change was made to the Main Dashboard revenue calculation.

Label updates:

- Main Dashboard finance heading now says accounting revenue.
- Main Dashboard revenue chart title now says `Accounting Revenue vs Expenses`.
- Main Dashboard chart legend now says `Accounting Revenue`.
- CEO Dashboard invoice chart title now says `Monthly Invoiced Revenue`.
- CEO Dashboard invoice summary now says `Invoiced Revenue Overview`.

## Checks Not Completed

Full browser console and horizontal-overflow checks were not completed in this local rehearsal because no real staging browser host was available. Rendered-page HTTP and duplicate-ID checks passed for the changed surfaces.

## Data Safety

Confirmed in staging:

- Existing `created_at` values were not modified.
- `invoice_date` and `opportunity_date` are nullable.
- No accounting entries were created.
- No payments were created.
- No production orders were created.
- No workflow migrations beyond 0185 were created.
- No production database was touched.

## Deployment Recommendation

READY FOR PRODUCTION DEPLOYMENT PREPARATION.

Proceed with the normal production safety process:

1. Create a fresh production backup.
2. Confirm migration 0185 is visible and unapplied.
3. Apply migration 0185 only after backup verification.
4. Run Django checks and smoke tests.
5. Verify CEO/Financial/AR/Production Profit invoice revenue dating.
6. Verify Main Dashboard still shows accounting revenue from `AccountingEntry.date`.
