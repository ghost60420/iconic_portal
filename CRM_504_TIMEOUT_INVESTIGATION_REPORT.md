# CRM 504 Timeout Investigation Report

Report date: 2026-07-28

## Incident Time

- First confirmed Calendar 504: 2026-07-28 17:02:17 UTC
  (10:02:17 America/Vancouver).
- Repeated Calendar 504s and worker timeouts continued through at least
  2026-07-28 17:14:22 UTC.

## Confirmed Cause

The Calendar GET handler called `send_due_event_reminders()` inline. One due
reminder attempted to open an SMTP connection from the synchronous Gunicorn
worker. That connection did not complete, so Nginx returned 504 and Gunicorn
killed the blocked worker at its 120-second timeout.

The Calendar database/render path is not the cause. With reminder sending mocked
out against the live database, Calendar returned HTTP 200 in 513.63 ms with 11
queries. The slowest database query took 3 ms.

## Server Health

- CPU: NORMAL - 93.8% idle during investigation.
- Memory: NORMAL - 544 MB used and 3,047 MB available of 3,839 MB.
- Swap: NORMAL - 243 MB used of 1,024 MB; no active memory pressure.
- Disk: NORMAL - root volume 63% used, 15 GB available.
- Load average: NORMAL - 0.38 / 0.14 / 0.10.
- Gunicorn: UNHEALTHY DURING INCIDENT - service remained active, but Calendar
  requests repeatedly blocked and workers timed out/recycled.
- Nginx: HEALTHY - service active; 504s were caused by upstream Gunicorn
  response timeouts, not an Nginx process/configuration failure.
- Database: HEALTHY - 119,476,224-byte SQLite database, immediate read check,
  normal locking mode, no `database is locked` evidence.
- Background jobs: NORMAL - the expected ten-minute inbox sync briefly used CPU
  and completed. No test, pytest, migration, development server, database copy,
  FedEx refresh, or long-running shell process remained.

## Log Findings

Nginx logged repeated:

`upstream timed out while reading response header from upstream`

Gunicorn logged repeated:

`WORKER TIMEOUT` and `Error handling request /calendar/`

The application traceback ended at:

`calendar_list -> send_due_event_reminders -> send_mail -> SMTP socket connection`

No evidence was found for:

- out-of-memory or killed-process events
- database locks
- slow database queries
- connection refused
- too many open files
- disk or I/O errors

## Slow Request Findings

- Scope: Calendar only.
- Recent authenticated access logs showed HTTP 200 for Main Dashboard,
  Shipments, and Production while Calendar produced repeated 504 responses.
- Unauthenticated login response: HTTP 200 in 0.280 seconds.
- Calendar application render without SMTP: HTTP 200 in 0.514 seconds.
- Calendar queries: 11, with no repeated query shapes or N+1 pattern.
- Calendar events in database: 197.
- Calendar events loaded for the current grid: 20.
- Reminder candidates scanned: 65.
- Reminders due during investigation: 1.
- External call on Calendar GET: one synchronous SMTP reminder attempt.
- No loop over all customers, leads, opportunities, or employees was found.

## Hotfix

The isolated hotfix preserves Calendar reminder selection, timing, recipient,
subject/body, and sent-state rules. It moves the SMTP operation to the existing
Calendar background-email pattern and applies an eight-second bounded SMTP
timeout outside the HTTP request. Reminder state is saved only after successful
delivery; timeout/failure preserves the unsent state.

Deployment:

- Source branch: `calendar-504-hotfix`
- Production branch: `living-catalog-production-deployment`
- Hotfix commit: `490df39 Prevent Calendar SMTP request timeouts`
- Deployed: 2026-07-28 17:17:05 UTC

Files changed:

- `crm/views.py`
- `crm/services/calendar_notifications.py`
- `crm/tests/test_calendar_invites_reminders.py`
- `CRM_504_TIMEOUT_INVESTIGATION_REPORT.md`

No models, migrations, Calendar UI, event creation, permissions, attendee rules,
business modules, Nginx configuration, or production data were changed.

## Verification

Pre-deployment:

- Python compilation: PASS
- `python manage.py check`: PASS
- `python manage.py makemigrations --check --dry-run`: PASS
- Focused Calendar tests: 7 PASS
- `git diff --check`: PASS

Post-deployment:

- Production `python manage.py check`: PASS
- Main Dashboard: HTTP 200, 401.40 ms
- Calendar: HTTP 200, 474.14 ms while the reminder sender was deliberately
  mocked to take five seconds
- Shipments list: HTTP 200, 43.97 ms
- Shipping detail: HTTP 200, 45.12 ms
- Production list: HTTP 200, 214.85 ms
- Production detail: HTTP 200, 111.35 ms
- Calendar queries: 12
- Slowest Calendar database query: 2 ms
- Calendar N+1 result: no repeated query shapes
- SQLite read/lock check: PASS
- New Gunicorn errors after restart: none
- New Nginx upstream errors after restart: none

## Data Safety Baseline

- Customers: 633
- Leads: 939
- Opportunities: 120
- Invoices: 40
- Payments: 23
- Production orders: 82
- Shipments: 22
- Accounting entries: 146
- Calendar events: 197

These counts must remain unchanged after deployment.

Post-deployment counts matched the baseline exactly for every listed model.
Calendar event count remained 197. The verification sender was mocked, so no
Calendar event or reminder field was changed by the verification requests.

## Services Restarted

Gunicorn was restarted once at 2026-07-28 17:17:05 UTC.

Nginx, Redis, Celery, the database, and all unrelated services were not
restarted.

## Calendar Response Time

- Before: repeated Nginx 504 responses; Gunicorn workers blocked up to 120
  seconds before forced timeout.
- Application path without SMTP: 0.514 seconds.
- After: HTTP 200 in 0.474 seconds while a five-second reminder sender ran
  outside the request path.

## Remaining Risks

- SMTP availability can still affect reminder delivery, but it will no longer
  block Calendar HTTP requests.
- The background transport uses the existing in-process daemon-thread pattern;
  a worker termination during delivery can leave a reminder unsent for retry.
- The page performs 11 queries, above the general ten-query dashboard target,
  but the database portion is fast and not involved in this incident.

## Final Status

**CRM PERFORMANCE RESTORED**

FedEx deployment remains stopped. Migration `crm.0192` remains unapplied.
