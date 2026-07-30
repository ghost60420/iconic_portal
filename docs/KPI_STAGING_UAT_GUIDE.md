# KPI Private Staging UAT Guide

## Safety

- Confirm the hostname is `kpi-staging.100-51-11-48.sslip.io`.
- Use only the staging accounts in the access guide.
- Do not enter real customer, payroll, payment, or provider data.
- Do not enable a scheduler or external provider.
- Use the manual automation command only with `--dry-run` unless the test run
  will be cleaned immediately.

## Role Checks

| Role | Expected KPI access |
| --- | --- |
| CEO | All KPI pages, setup, reviews, dashboards, intelligence, reports |
| Manager | Own performance, dashboard, assigned review queue |
| Employee | Own performance, dashboard, reports, notifications |
| Multi-role employee | Own 60/25/15 assignment and role breakdown |
| Director | Department review, approval, rejection, locking |
| HR | Authorized review and performance scope; private bonus values withheld |
| Accounts | Own performance; KPI setup returns `403` |

Server permission checks remain authoritative even when a menu item is hidden.

## Full Workflow

Use a staging employee and a new period:

1. Confirm a single-role assignment totals 100 percent.
2. Confirm a multi-role assignment totals 100 percent.
3. Open a Draft review from Review Queue.
4. Enter all KPI results.
5. Save Draft.
6. Submit Review.
7. As Director, Start Review and Reject with a reason.
8. As Manager, correct the review and save it.
9. Resubmit.
10. As Director, Start Review and Approve.
11. Lock Review.
12. Confirm `Snapshot verified`.
13. Confirm KPI/Executive Dashboard changes.
14. Confirm Executive Intelligence changes.
15. Confirm Bonus Readiness.
16. Confirm a CRM-only KPI notification.
17. export PDF, Excel, CSV, and Print.

The deployment validation completed this workflow with review `7`.

## Manual Automation

Dry run:

```bash
cd /home/ec2-user/iconic_kpi_staging
set -a
source config/staging.env
set +a
venv/bin/python manage.py run_kpi_staging_automation \
  --schedule daily \
  --dry-run
```

One explicit run:

```bash
venv/bin/python manage.py run_kpi_staging_automation --schedule daily
```

Cleanup its CRM-only test notifications:

```bash
venv/bin/python manage.py run_kpi_staging_automation --cleanup-run RUN_ID
```

No recurring job is configured.

## Bug Template

Create one record per issue:

| Field | Value |
| --- | --- |
| Page | |
| User role | |
| Steps | |
| Expected result | |
| Actual result | |
| Screenshot | |
| Severity | Critical / High / Medium / Low |
| Date | |
| Status | New / Confirmed / In progress / Resolved / Retest |

Severity:

- Critical: staging unavailable, data/security breach, or UAT cannot continue.
- High: primary KPI workflow is blocked.
- Medium: workflow works with a material usability or correctness problem.
- Low: cosmetic or minor issue with a clear workaround.

Do not fix unrelated CRM features during UAT unless they block KPI testing.
