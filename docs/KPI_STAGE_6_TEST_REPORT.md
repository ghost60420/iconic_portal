# KPI Stage 6 Test Report

## Environment

- Branch: `feature/kpi-stage-6-bonus-engine`
- Base commit: `197b50c5d18e4223d86ab75b0f8423cd6744906a`
- Migration: `crm.0195_kpi_bonus_engine`
- Production/AWS access: none
- Original application database write: none

## Focused Coverage

- Individual, team, and company weighted scores
- The configured `60/25/15` formula
- Critical Red blocked and allowed behavior
- Minimum and maximum scores
- Bonus floor, cap, and attendance multiplier
- Inactive employee and historically non-eligible assignment
- Historical eligibility after a later assignment change
- Pending and rejected reviews
- Missing team/company sources
- Disabled bonus configuration
- Invalid approved snapshot digest
- Effective rule and weight-profile versions
- Published configuration immutability
- Stored calculation immutability and digest verification
- Repeated creation returning the historical result
- Constant-query three-source evaluation

## Results

- Focused Stage 6 tests: PASS (`21 of 21`)
- Stage 5 baseline: PASS (`849 of 849`)
- Full suite: PASS (`870 of 870` in `458.169s`)
- Django system check: PASS
- Python compile check: PASS
- `makemigrations --check --dry-run`: PASS
- Fresh complete migration graph: PASS through `crm.0195`
- Populated copied database forward migration: PASS
- Populated rollback to `crm.0194`: PASS
- Populated reapply: PASS
- SQLite integrity: PASS (`ok`)
- Foreign-key violations: zero
- Protected populated record counts: unchanged
- Source database checksum before/after:
  `bce604296b39cc6d5dd1bb005b0089f0e09f78a53571645e6a899c9fc2a93869`

## Performance

- Three-source evaluation query count: `3`
- N+1 result: not detected
- Cold evaluation: `2.990 ms` (`3` queries)
- Warm evaluation average: `1.615 ms` (`3` queries, 50 runs)
- Warm evaluation range: `1.522-2.114 ms`
- Measurement database: isolated populated Stage 5 copy after `crm.0195`

## Security

- No routes, APIs, pages, or employee bonus visibility
- No payroll, payment, commission, or currency-conversion integration
- No permission or middleware changes
- Stage 4 calculation engine unchanged
- Stage 5 review workflow unchanged
- No production system or AWS resource accessed
- No database, media, environment, or private backup file is included
