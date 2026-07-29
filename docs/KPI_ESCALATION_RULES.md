# KPI Escalation Rules

## Model

`KPIEscalationRule` belongs to one notification policy version and stores:

- Event type
- Escalation stage
- Signed trigger offset in days
- Severity
- Recipient scopes
- Active state

Recipient scopes are `employee`, `manager`, `director`, `hr`, and `executive`.
They resolve through existing active users, CRM groups, employee managers, and
department relationships. A manager assignment never grants access beyond the
related employee.

## Timing

Review events match an exact configured offset. Progressive events such as
Critical Red, unresolved attention, bonus readiness, and manager workload use
the highest escalation stage whose delay has elapsed. This prevents every
earlier escalation from being resent on later runs.

The implementation supports the requested seven-, three-, and one-day
reminders, due-date reminders, and one- and seven-day overdue escalation, but
does not seed those values as final company policy.

## Severity and History

A severity change creates a distinct event. Existing notification and
escalation history remains immutable. Escalation creation is audited without
copying private message content into the audit record.
