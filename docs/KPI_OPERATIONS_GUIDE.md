# KPI Operations Guide

## Daily Checks

1. Confirm the application and authenticated `/system-health/` page load.
2. Check KPI automation runs for Completed or bounded Failed status.
3. Review overdue, Critical Red, data-quality, and manager-queue alerts.
4. Confirm notification counts are plausible and duplicates are blocked.
5. Review HTTP errors, database errors, response time, disk, memory, and CPU.
6. Keep external email, SMS, WhatsApp, payroll, and payment delivery disabled.

## Weekly Checks

1. Review manager and department summaries.
2. Check unresolved Red and Yellow actions.
3. Confirm notification history growth and query latency remain bounded.
4. Verify no unpublished policy affects current records.
5. Confirm audit events exist without private report content.

## Monthly And Quarterly Checks

Confirm assignment dates, completed and locked reviews, snapshot digests,
missing periods, policy versions, and bonus-readiness inputs. Bonus readiness
is not payment approval. Do not combine or convert currencies.

## Failure Handling

For a failed run, record run ID, schedule, source date, retry count, failure
type, duration, and sanitized log reference. Do not retry without limit. A
failure must not block normal CRM requests.

For permission exposure, snapshot mismatch, lost records, changed IDs,
foreign-key errors, duplicate notifications, or unexpected external delivery:

1. Stop the KPI release or exact KPI scheduler entry.
2. Preserve logs and database state.
3. Do not edit migration history or approved records.
4. Escalate to the release and security owners.
5. Follow the rollback or disaster-recovery guide.

## Capacity

Alert before disk exhaustion. A migration requires room for the database,
temporary table rebuilds, backup, restore verification, logs, and static
files. The Stage 11 workstation had only about 1.3 GiB free, so it is not an
approved production rehearsal host.
