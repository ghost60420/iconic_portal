# KPI Automation Schedules

## Command

The repository already uses bounded Django management commands for scheduled
work. Stage 9 therefore adds:

```bash
python3 manage.py run_kpi_automation --schedule daily
python3 manage.py run_kpi_automation --schedule weekly
python3 manage.py run_kpi_automation --schedule monthly
python3 manage.py run_kpi_automation --schedule quarterly
python3 manage.py run_kpi_automation --schedule annual
```

`--date YYYY-MM-DD` is available for controlled testing and recovery. No cron,
systemd, Celery, or cloud scheduler configuration is added in this stage.

## Responsibilities

| Schedule | Evaluation |
| --- | --- |
| Daily | Review timing, approval, correction, lock, improvement, Red/Yellow/Green, bonus readiness, manager queue, executive state |
| Weekly | Authorized manager and department intelligence summary |
| Monthly | Company KPI summary |
| Quarterly | Company KPI and immutable bonus-readiness summary |
| Annual | Company KPI summary |

Enabled schedules are controlled by the effective published rule.

## Runs and Retries

Every attempt is represented by one `KPIAutomationRun` with source date,
configured batch size, retry limit, counters, timestamps, and sanitized final
failure information. Retry limits are bounded from zero through five. The
command contacts only the local database and CRM Notification Center.

Production scheduler commands, environment, working directory, and timing
must be separately approved during Stage 10.
