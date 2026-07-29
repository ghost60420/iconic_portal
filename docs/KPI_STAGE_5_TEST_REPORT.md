# KPI Stage 5 Test Report

## Environment

- Branch: `feature/kpi-stage-5-performance-ui`
- Base commit: `aa3354d35d1d76b463730e828513d235c8c8821c`
- Migration: `crm.0194_kpi_performance_reviews`
- Production/AWS access: none
- Original repository database write: none

## Focused Coverage

The Stage 5 tests cover:

- Employee own-history access and cross-employee denial
- Assigned-manager scope and unrelated-manager denial
- Director department scope, HR read-only scope, CEO and Super Admin scope
- Draft creation, Stage 4 calculation, submit, review, approve, reject, and lock
- Ordered workflow and self-approval protection
- Model and service read-only enforcement
- Service-only immutable approval history
- Critical Red score preservation and status override
- Formula, calculation, template, and assignment versions
- Approved snapshot completeness and digest verification
- Frozen historical display after a live template rename
- Employee profile Performance tab
- Public-route denial and server-side POST authorization
- Paginated stored history without recalculation
- Employee Performance, review detail, and manager queue query budgets

## Migration Verification

- Fresh complete graph through `crm.0194`: PASS
- Populated copied database forward migration: PASS
- Rollback from `crm.0194` to `crm.0193`: PASS
- Reapply `crm.0194`: PASS
- SQLite integrity check: PASS (`ok`)
- Foreign-key check: PASS (zero violations)
- Protected populated counts before/after: unchanged
- Original `db.sqlite3` checksum before/after:
  `ac0c3e3eda99cce10d69a475fe87f71741a835bdd70d96bf6b8ceec2a7669073`

## Results

- Focused Stage 5 tests: PASS (`26 of 26`)
- Stage 4 baseline: PASS (`823 of 823`)
- Full suite: PASS (`849 of 849` in `389.484s`)
- Django system check: PASS
- Python compile check: PASS
- `makemigrations --check --dry-run`: PASS
- `git diff --check`: PASS

## Performance

| Page | Cold queries/time | Warm queries/time | Budget |
| --- | --- | --- | --- |
| Employee Performance | 16 / 57.7 ms | 8 / 6.7 ms | 8 |
| Review detail | 9 / 11.7 ms | 8 / 7.4 ms | 8 |
| Manager review queue | 11 / 13.8 ms | 10 / 8.3 ms | 10 |

The initial partial implementation used 9 warm queries for both detail views.
Reusing the already selected access record reduced each to 8 without changing
the shared permission decision. Review entries, transitions, employees, and
managers use bounded `select_related` or `prefetch_related` queries.

## Security

- No public endpoint or API was added.
- No permission role or middleware was changed.
- Approved and locked records are immutable.
- Employees cannot see draft, submitted, or under-review records.
- No bonus, payroll, commission, notification, or protected CRM logic changed.
- No database, media, environment, secret, or private backup file is included.
- Production and AWS were not accessed.
