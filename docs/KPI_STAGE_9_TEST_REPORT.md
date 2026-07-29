# KPI Stage 9 Test Report

## Scope

Stage 9 tests cover all requested reminder types, approval and correction
events, immutable-record alerts, Red/Yellow/Green handling, bonus privacy,
manager and executive alerts, deduplication, escalation, all role scopes,
action-link scope, retained history, audit, retries, command execution,
provider isolation, batch growth, cache behavior, and mobile controls.

## Results

| Check | Result |
| --- | --- |
| Stage 9 focused tests | PASS - 29 of 29 |
| Existing Notification Center and KPI regression | PASS - 240 of 240 |
| Full CRM regression | PASS - 950 of 950 in 475.379 seconds |
| Django system check | PASS |
| Migration drift | PASS - no model changes detected |
| Fresh migration through `crm.0197` | PASS |
| Populated copied database migration | PASS |
| Populated rollback to `crm.0196` | PASS |
| Populated reapplication | PASS |
| SQLite integrity | PASS - `ok` |
| Foreign keys | PASS - 0 violations |
| Protected record counts and ID digests | PASS - unchanged |

## Performance

Measured on the populated validation copy:

| Measurement | Queries | Time |
| --- | ---: | ---: |
| Create 1 notification | 8 | 11.612 ms |
| Create 50 notifications | 10 | 24.617 ms |
| Notification Center cold, including auth/session | 10 | 88.835 ms |
| Notification Center warm, including auth/session | 8 | 20.443 ms |
| Notification Center application queries | 3 | Included above |
| Cached header count | 0 | Below timer precision |

Fifty rows use only two more queries than one row. The query-shape assertion
passes and detects no N+1 growth.

## Database Safety

Migration `crm.0197_kpi_notifications_automation` is additive and reversible.
The untouched development database and source copy both retained checksum
`ac0c3e3eda99cce10d69a475fe87f71741a835bdd70d96bf6b8ceec2a7669073`.
Validation databases remain outside the repository.
