# KPI Monitoring Guide

## Required Signals

Monitor:

- application availability, 4xx/5xx rate, and response latency;
- application startup, exceptions, and migration errors;
- database connectivity, size, locks, integrity checks, and capacity;
- CPU, memory, disk usage, and process restarts;
- KPI automation status, duration, retry count, created count, and duplicates;
- notification queue growth and overdue Critical Red escalation;
- dashboard, Intelligence, review, and export latency;
- audit-event creation and snapshot verification failures.

## Health Endpoint

`/system-health/` is authenticated and restricted to CEO or Super Admin. It
must report the deployed commit, last backup, and last deployment through
`APP_VERSION` or `GIT_COMMIT`, `LAST_BACKUP_AT`, and `DEPLOYED_AT`.

Stage 11 local validation returned HTTP 200, but these metadata values were not
configured. Production monitoring is therefore not verified.

## Alert Thresholds

Production owners must approve concrete thresholds. At minimum, alert on:

- any sustained 5xx increase;
- failed migration or application startup;
- database or disk capacity approaching the approved floor;
- any KPI automation Failed result;
- retries reaching the published limit;
- missing expected daily or weekly run;
- unexpected notification growth or external delivery attempt;
- permission denial spike, snapshot digest mismatch, or audit-write failure;
- warm KPI pages exceeding their tested query budget.

## Dashboard Budgets

- KPI dashboard shell: 10 queries maximum.
- Employee Performance and review detail: 8 queries maximum.
- Intelligence shell: 10 queries maximum.
- Individual KPI or Intelligence widget: 5 queries maximum.
- Cached widget: 0 database queries where designed.
- Notification-domain list work: 5 queries maximum with no row-driven growth.

Record total request queries separately from domain queries because session,
authentication, global navigation, and permission checks are shared platform
costs.

## Ownership And Retention

Before deployment, name an application owner, database owner, security owner,
and on-call escalation path. Logs must exclude private comments, report
contents, bonus amounts, credentials, and export rows. Retention and access
must follow company policy.
