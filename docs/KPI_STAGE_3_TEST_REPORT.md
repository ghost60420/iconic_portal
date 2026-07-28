# KPI Stage 3 Test Report

## Environment

- Branch: `feature/kpi-stage-3-employee-assignments`
- Base commit: `6e2460524d7ddb46f5f2ef359f3731d9043337fc`
- Migration: `crm.0193_kpi_employee_role_assignments`
- Production/AWS access: none
- Repository database migration target: none

## Results

- Existing Stage 2 regression baseline: PASS (`766 of 766`)
- Focused Stage 3 assignment tests: PASS (`31 of 31`)
- Full suite: PASS (`797 of 797` in `318.413s`)
- Fresh complete migration graph: PASS
- Populated Stage 2 to Stage 3 migration: PASS
- Populated rollback to `crm.0192`: PASS
- Populated reapply of `crm.0193`: PASS
- Django system check: PASS
- `makemigrations --check --dry-run`: PASS
- Populated migration plan: PASS (no pending operations)
- SQLite integrity: PASS (`ok`)
- Foreign-key check: PASS (`0` violations)

## Assignment Coverage

PASS:

- One role at `100.00`
- Two and three roles totaling `100.00`
- Decimal weights
- Atomic multi-role reweighting
- Rejection of `90.00`, `110.00`, zero, negative, and over-100 weights
- Current, future, expired, inactive, and archived behavior
- Start/end date validation
- Historical date lookup
- Optional, valid, self, inactive, and archived manager cases
- Manager old/new history
- Deactivation and archival history
- Bonus eligibility filtering and history
- Duplicate-template overlap detection
- Missing ID rejection
- Database row constraints
- Assignment/history mutation protection
- Current assignment related-object query bound: one query for three roles

## Data Integrity

The protected-table dump signature was identical before forward migration,
after forward migration, after rollback, and after reapply:

`19ad00265555f261cf0711a0aac817114ac6e8e26b9fa5df96f792d32e643d13`

Migration `0193` created two empty tables and one ledger entry. It created no
employee assignment or history row and changed no existing primary key.

Authorization counts before and after were unchanged:

- Permissions: `674`
- Groups: `16`
- Group-permission assignments: `0`
- Users: `33`

Two content types were added for the new models. Both models declare no default
permissions in this stage.

## Security

- No public route or page was added.
- No bonus amount or private note is exposed.
- No login, group, `UserAccess`, or permission middleware changed.
- No database, patch, inventory, environment, or secret file is committed.
- History snapshots are append-only through the model layer.

## Not Tested

- Production database or production data
- AWS or deployment behavior
- Browser UI, because Stage 3 adds no UI
- Authorization workflows, which are deferred
- Calculation and bonus engines, which are not part of Stage 3

## Decision

Stage 3 model, service, migration, history, and regression gates pass. This
stage remains **NOT SAFE TO DEPLOY** as a standalone KPI system.
