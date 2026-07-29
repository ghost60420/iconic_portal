# KPI Notification Rules

## Versioned Policy

`KPINotificationRule` stores an effective-dated policy version:

- Enabled notification types
- Reminder offsets
- Improvement follow-up days
- Enabled daily, weekly, monthly, quarterly, and annual schedules
- Optional Green recognition
- Retry limit
- Batch size
- Source lookback

Rules begin as Draft. CEO or Super Admin may publish a complete rule through
the service. Published and retired versions are immutable. Publication is
blocked unless every enabled type has an active escalation rule.

## Supported Events

- Review open, due soon, due today, overdue, and approval pending
- Rejected review correction and locked review confirmation
- Improvement follow-up
- Critical Red, Yellow attention, and optional Green recognition
- Bonus ready, pending, and blocked
- Manager queue
- Executive intelligence
- Data quality

Review alerts read existing review state. Intelligence alerts consume Stage 8
results. Bonus alerts consume Stage 6 immutable calculations and omit money.

## Deduplication

Each recipient event has a SHA-256 key derived from:

- Notification type and recipient
- Source event, record type, and record ID
- Review period
- Severity
- Due date
- Rule code and version
- Escalation stage
- A hash of the source state

The database enforces key uniqueness. The service checks in batches and also
uses conflict-safe inserts. A new severity, period, policy version, escalation
stage, or source state may create a new historical event; an unchanged event
does not.

## Privacy

Messages are stripped of HTML and length bounded before storage. Private KPI
notes and bonus amounts are not copied into notifications or normal logs.
Every event is assigned to one active user, and visibility is always filtered
by that recipient on the server.
