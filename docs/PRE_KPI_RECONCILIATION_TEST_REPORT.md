# Pre-KPI Reconciliation Test Report

## Scope

- Test date: 2026-07-28
- Branch: `chore/pre-kpi-reconciliation`
- Approved base commit: `1084fc99be3fade235ca0bfa73673a3a44b7ff3e`
- Standalone database repair commit: `cf46cf9`
- Source base: `origin/calendar-504-hotfix`
- Production/AWS access: not used
- KPI code: not created

## Repair Execution Results

- Guarded compatibility repair restored the missing post-`0126`
  `crm_whatsappthread` and `crm_whatsappmessage` tables on an original-shaped
  populated copy. CRM `0142` then applied successfully.
- A repaired candidate was built at the source ledger boundaries, populated
  with 139 shared source tables and original primary keys, and migrated through
  CRM `0191`, Marketing `0012`, and WhatsApp `0005`.
- All 56 missing entries applied normally. No `--fake` operation was used.
- Populated rollback and reapply passed across all 56 entries.
- Integrity result: `ok`; foreign-key violations: 0.
- Every source primary key remains present. Source business-table counts are
  unchanged.
- Targeted affected suite: 103 of 103 passed.
- Stage 1.5 full suite: **760 of 760 passed in 309.382 seconds**.
- Populated warm queries improved from 18 to 14 for Employee Profile and from
  92 to 48 for the Main Dashboard.

The detailed failures below are the initial audit baseline and are retained as
historical evidence. They are superseded by this executed repair.

## Git Checks

### PASS

- Original branch, commit, upstream, staged, modified, untracked, and migration
  inventories captured.
- Safety branch `backup/pre-kpi-reconciliation-20260728` exists at original
  commit `2217ec1`.
- Binary patch reverse-apply check passed.
- SHA-256 verification passed for all 331 original untracked files.
- `git fetch origin` completed without merge, rebase, checkout, or working-tree
  change.
- Local `main` is 84 behind, 0 ahead, and has not diverged from
  `origin/main`.
- Clean reconciliation branch was created from the approved base in a separate
  worktree.
- Clean branch has no unmerged paths or accidental deletions.
- No force operation was used.

### INITIAL FAILURE - REPAIRED

- None in Git mechanics.

### NOT TESTED

- Remote push and pull request.
- Current production worktree/commit through direct server inspection.

## Django and Migration Checks

### PASS

```text
python3 manage.py check --settings=iconic_site.settings_testdb
System check identified no issues (0 silenced).

python3 manage.py makemigrations --check --dry-run \
  --settings=iconic_site.settings_testdb
No changes detected.
```

- Approved migration plan applied to a brand-new database in 33.63 seconds.
- Fresh database reached CRM `0191`, Marketing `0012`, WhatsApp `0005`, and all
  other application leaves.
- Fresh database integrity: `ok`.
- Fresh database foreign-key violations: 0.
- Approved CRM `0191` and `0190` rolled back to `0189`.
- The seven Library permission columns were absent after rollback.
- Approved CRM `0190` and `0191` reapplied.
- The seven Library permission columns were restored.
- Post-reapply integrity: `ok`; foreign-key violations: 0.
- Final migration plan on the fresh test database: no planned operations.

### FAIL

- A SQLite online backup of the current development database cannot apply the
  approved graph. It fails at `crm.0142`:

```text
django.db.utils.OperationalError:
no such table: crm_whatsappmessage
```

- The copied database ledger remains at `crm.0141`.
- The copied database does not match the approved schema.
- Populated-data rollback is not proven. Several RunPython reverse functions do
  not restore previous data values.

### NOT TESTED

- Production backup restore and migration rehearsal.
- PostgreSQL or any database engine other than the repository's SQLite setup.

## Full Test Suite

Command:

```text
python3 manage.py test --settings=iconic_site.settings_testdb --verbosity 1
```

Stage 1.5 result:

```text
Ran 760 tests in 309.382s
OK
```

- 760 tests passed.
- The Marketing calendar test now uses `timezone.localdate()` and requests the
  due date's selected month, preserving the original title and ISO-date
  assertions across a month boundary.

## Protected Regression Results

### PASS

- Employee login/status: employee status and suspended-login tests passed.
- Employee profiles: create, edit, archive, restore, identity, hierarchy, and
  audit tests passed.
- Role management: multi-role, role setup, self-removal protection, and
  role-page tests passed.
- Permissions: employee, sales, production, accounting, Library, operations,
  and read-only permission tests passed.
- Payroll screen: Bangladesh staff list/month/generate/edit tests passed.
- Bonus field: payroll UI and financial-format tests covering `bonus_bdt`
  passed.
- Sales commissions: SalesCommission currency/metrics plus QuickCosting
  commission tests passed.
- Leads: ownership, scope, pipeline, activity, and list query tests passed.
- Opportunities: workflow, assignment, stage, historical-date, and conversion
  tests passed.
- Accounts: accounting RBAC, attachment, Bangladesh cleanup, and finance tests
  passed.
- Sampling: sampling operational-status and sample-shipment workflow tests
  passed.
- Production: creation, operational status, permissions, profit, purchase
  order, and payment-gate tests passed.
- Quotations: costing engine, QuickCosting, approval, recall, and invoice
  workflow tests passed.
- Invoices: creation, currency, archive, payment, opportunity, and production
  conversion tests passed.
- Payments: delete/audit, payment threshold, local-sewing, and finance tests
  passed.
- Main, CEO, sales, finance, and operations dashboards: tests passed.
- Notifications: recipient scope, duplicate cleanup, permission, shipment,
  mention, and Calendar failure-path tests passed.
- File uploads: accounting attachments, reference images, and Living Catalog
  image validation tests passed.
- Marketing Intelligence calendar: selected-month and timezone-boundary test
  passed.

### FAIL

- None.

### NOT TESTED

- Browser-level live server smoke tests.
- External provider API calls.
- Production data and production media.

## Performance Check

### Before reconciliation

The old dirty source was tested against a copied current development database.
It was not possible to obtain valid response timings:

| Screen | Result |
|---|---|
| Employee list | Route does not exist in the old checkout |
| Employee profile | Route does not exist in the old checkout |
| Main dashboard | HTTP 500: missing `crm_lead.is_archived` |
| Lead list | HTTP 500: missing `crm_lead.is_archived` |
| Opportunity list | HTTP 500: incompatible deferred/select-related `lead` field |
| Production list | HTTP 500: missing `crm_productionorder.source_quotation_id` |
| Invoice list | HTTP 500: missing `crm_invoice.is_archived` |

### Approved base with fresh schema

These are local in-process Django Client measurements on an empty, freshly
migrated SQLite database with one synthetic superuser. "Cold" is the first
request in that process and "warm" is the immediate second request. They are not
production measurements.

| Screen | Cold | Warm | Cold queries | Warm queries | Result |
|---|---:|---:|---:|---:|---|
| Employee list | 59.80 ms | 4.15 ms | 7 | 5 | HTTP 200 |
| Employee profile | 41.95 ms | 29.88 ms | 19 | 18 | HTTP 200 |
| Main dashboard | 70.04 ms | 42.99 ms | 90 | 90 | HTTP 200 |
| Lead list | 16.94 ms | 9.47 ms | 6 | 6 | HTTP 200 |
| Opportunity list | 23.62 ms | 18.72 ms | 5 | 5 | HTTP 200 |
| Production list | 13.77 ms | 9.39 ms | 4 | 4 | HTTP 200 |
| Invoice list | 8.91 ms | 5.42 ms | 8 | 8 | HTTP 200 |

### Stage 1.5 repaired populated database

Measurements use the same populated repair copy and an authenticated
superuser. Cold is the first request after clearing the process-local cache.
Warm time is the median of five subsequent requests.

| Screen | Before cold | After cold | Before warm | After warm |
|---|---:|---:|---:|---:|
| Employee Profile queries | 21 | 17 | 18 | 14 |
| Employee Profile response | 78.58 ms | 63.50 ms | 25.78 ms | 17.01 ms |
| Main Dashboard queries | 94 | 50 | 92 | 48 |
| Main Dashboard response | 100.28 ms | 89.22 ms | 44.98 ms | 35.74 ms |

Findings:

- Employee profile exceeds the eight-query detail-page budget.
- Main dashboard exceeds the ten-query dashboard budget.
- The final measurements use the repaired populated database copy.
- The compact Employee Profile sales summary has a five-query regression
  bound and returns the same counts, native-currency revenue, and closing
  ratio as the canonical sales KPI service.
- Repeated lead, opportunity, invoice, payroll, shipment, lifecycle, and
  automation counts now use shared aggregates or already-loaded rows.
- Query normalization found no row-driven repeated query pattern on either
  page. The remaining repeated automation queries are fixed category queries,
  not N+1 iteration.
- Existing bounded-query tests passed for employee list/roles, lead queues,
  operations/CEO/sales services, notifications, and several reports.
- Production and invoice list growth were not scale-tested in this stage.

## Final Test Decision

Populated forward migration, rollback/reapply, data-integrity, and all 760
regression tests pass. The repaired database is installed only in the
reconciliation worktree, the original database checksum remains unchanged,
and the remaining query-budget gap is documented.

**SAFE TO START KPI STAGE 2**
