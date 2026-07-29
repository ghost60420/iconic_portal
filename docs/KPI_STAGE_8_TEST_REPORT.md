# KPI Stage 8 Test Report

## Scope

Stage 8 adds focused tests for intelligence policy, snapshot-only analytics,
all role scopes, alerts, trends, data quality, bonus privacy, signed actions,
all report types and formats, audit events, query budgets, independent widget
loading, mobile assets, migration safety, and regression.

## Results

| Check | Result |
| --- | --- |
| Stage 8 focused tests | PASS - 29 of 29 |
| Django system check | PASS |
| Migration drift | PASS - no model changes detected |
| Fresh migration through `crm.0196` | PASS |
| Populated copied database migration | PASS |
| Populated rollback to `crm.0195` | PASS |
| Populated reapplication | PASS |
| SQLite integrity | PASS - `ok` |
| Foreign keys | PASS - 0 violations |
| Protected record counts and ID ranges | PASS - unchanged |
| Desktop/tablet/mobile widget rendering | PASS |
| Independent executive widgets | PASS - 13 of 13 |
| Canvas rendering | PASS - 3 nonblank trend canvases per viewport |
| Horizontal overflow and overlap | PASS - none detected |
| KPI Stages 2-8 regression | PASS - 161 of 161 |
| Full CRM regression | PASS - 921 of 921 in 391.874 seconds |

## Performance

| Measurement | Queries | Time |
| --- | ---: | ---: |
| Cold Intelligence Center shell | 6 | 56.498 ms |
| Warm Intelligence Center shell | 5 | 6.763 ms |
| Uncached company-health service widget | 4 | 10.029 ms |
| Cached service widget | 0 | 0.133 ms |

The no-N+1 tests compare query SQL and row-count growth. Adding a visible
employee result did not increase the widget query count.

## Database Safety

Migration `crm.0196_kpi_intelligence_rules` creates one empty versioned rule
table. It does not alter protected tables, seed policy, or perform a data
operation. The untouched source copy checksum remained
`e9e17791d550051c6f68f3da4bb1e8beba779430c400f97b6d0854c2db2bbe10`.

Validation copies and screenshots are outside the repository and are not
application commit candidates.
