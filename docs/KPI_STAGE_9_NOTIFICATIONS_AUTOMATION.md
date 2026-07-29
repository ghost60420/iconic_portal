# KPI Stage 9 Notifications and Automation

## Scope

Stage 9 extends the existing CRM Notification Center with KPI workflow,
intelligence, and bonus-readiness alerts. It does not create a second inbox,
contact an external provider, recalculate KPI results, recalculate bonus
results, change permissions, or run a background process.

The implementation is split between:

- `crm.services.kpi_notifications`: delivery, deduplication, visibility,
  dismissal, read/view audit, and rule publication.
- `crm.services.kpi_automation`: review, intelligence, bonus, summary,
  escalation, recipient, retry, and run orchestration.
- `run_kpi_automation`: a bounded management command for an approved existing
  scheduler to call.

## Approved Sources

Automation reads:

1. Stage 5 review records and immutable approved snapshots.
2. Stage 6 immutable bonus calculation records.
3. Stage 8 role-scoped intelligence results.
4. Existing employee, manager, department, and CRM role relationships needed
   for recipient authorization.

It does not call the Stage 4 Calculation Engine or change any source record.
Bonus messages contain readiness state and reason only; no amount is included.

## Notification Center

KPI alerts are stored as existing `AutomationNotification` rows assigned to a
specific active user. `KPINotificationEvent` adds immutable source, policy,
severity, review, employee, manager, department, version, and deduplication
metadata. The existing authenticated Notification Center provides KPI and KPI
History filters, read, open, dismiss, and retained history actions.

Dismissal resolves the active CRM notification but preserves both records.
The existing delete-read action excludes KPI history. Destination views still
perform their own authorization checks.

## Failure Safety

Every automation invocation creates a `KPIAutomationRun`. Exceptions are
captured, sanitized, retried only to the configured limit, and returned as a
failed run. A failure does not run an infinite loop and is not raised through
normal CRM page requests.

No final company notification policy is seeded. Until an approved rule is
published, command runs fail closed and record the missing configuration.

## Migration and Rollback

Migration `crm.0197_kpi_notifications_automation` creates four new tables,
their indexes, foreign keys, uniqueness rules, and check constraints. It has
no data operation and does not alter a protected table.

Rollback removes only the four empty Stage 9 tables. Historical Stage 9 events
must be retained or exported before an authorized rollback after real use.
Fresh, populated-copy, rollback, and reapplication checks passed. The original
development database remained unchanged.

## Limits

- No configuration UI is included.
- No email, SMS, WhatsApp, push, or provider delivery is included.
- No scheduler entry is installed by this stage.
- Bonus approval and employee bonus visibility remain future workflows.
