# KPI Automation Production Setup

## Status

No scheduler entry was created or activated. These instructions require a
separate production approval after the notification policy is Published.

## Commands

Run from the confirmed project directory and application environment:

```bash
python3 manage.py run_kpi_automation --schedule daily
python3 manage.py run_kpi_automation --schedule weekly
python3 manage.py run_kpi_automation --schedule monthly
python3 manage.py run_kpi_automation --schedule quarterly
python3 manage.py run_kpi_automation --schedule annual
```

Controlled recovery/testing may supply `--date YYYY-MM-DD`.

## Proposed Frequencies

| Command | Frequency | Expected result |
| --- | --- | --- |
| `--schedule daily` | Daily | Review, overdue, Critical Red, Yellow, queue, and readiness checks |
| `--schedule weekly` | Weekly | Manager and department summaries |
| `--schedule monthly` | Monthly | KPI summary |
| `--schedule quarterly` | Quarterly | Immutable bonus-readiness summary |
| `--schedule annual` | Annual | Annual KPI summary |

The approved scheduler timezone must match `America/Vancouver` unless the
published policy and operations owner explicitly select another zone.

## Prerequisites

1. Notification policy is Approved and Published.
2. No duplicate scheduler entry exists.
3. Working directory, Python environment, log directory, and service account
   are confirmed.
4. The service account can read only required application configuration and
   write application logs.
5. Health-check and alert owners are named.

## Scheduler Entry Pattern

Use the existing approved scheduler. Do not invent a new daemon. A cron-like
entry should call a wrapper that changes to the confirmed project directory,
loads the approved environment, runs one bounded command, and appends sanitized
stdout/stderr to the confirmed log path.

Required wrapper behavior:

```bash
set -eu
cd "$APPROVED_PROJECT_DIRECTORY"
python3 manage.py run_kpi_automation --schedule "$1"
```

Do not place secrets on the command line. Do not write private notification
content to scheduler logs.

## Monitoring

For each run verify:

- A `KPIAutomationRun` was created.
- Status is Completed, Duplicate, or a bounded Failed result.
- Retry count did not exceed the published rule.
- Created and duplicate counters are plausible.
- No external provider log or network delivery occurred.
- Database query growth remains bounded.

Alert on failed runs, repeated missing policy, unusual duration, growing
manager queues, or absent expected runs.

## Safe Disable

Disable only the exact KPI scheduler entries, record actor/time/reason, and
confirm no `run_kpi_automation` process remains. Do not stop email sync,
Lead Brain, marketing, WhatsApp, FedEx, or other CRM schedules.
