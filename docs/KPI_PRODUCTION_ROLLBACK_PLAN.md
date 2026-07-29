# KPI Production Rollback Plan

## Principle

Prefer application rollback while retaining additive KPI tables. Schema
rollback after policy, assignment, review, bonus, intelligence, or notification
data exists can delete historical records and requires a verified full database
restore.

## Before Release

1. Record current production commit and service state.
2. Create and verify a database backup.
3. Verify media/upload recovery.
4. Record database and protected-table counts.
5. Confirm rollback owner and maximum recovery time.

## Application Rollback

Use only the confirmed previous production commit:

```bash
cd "$APPROVED_PROJECT_DIRECTORY"
git status --short --branch
git checkout "$CONFIRMED_PREVIOUS_PRODUCTION_COMMIT"
python3 manage.py check
sudo systemctl restart "$APPROVED_APP_SERVICE"
sudo systemctl status "$APPROVED_APP_SERVICE" --no-pager
```

Leave additive KPI schema in place unless the database restoration plan is
being executed.

## Automation Disable

No scheduler is installed by Stage 10. If an operator later installs one,
disable that exact scheduler entry first and confirm no
`run_kpi_automation` process remains. Do not disable unrelated CRM jobs.

## Schema Rollback

The isolated migration reversal is:

```bash
python3 manage.py migrate crm 0197
```

This removes `crm_kpipolicyapproval` and its rows. It is not the normal
production rollback after policy creation. Use it only with explicit approval
and a verified backup.

Reapply:

```bash
python3 manage.py migrate crm 0198
```

Reapplication recreates the table but does not restore deleted approval rows.

## Full Database Restore

Stop application writes, preserve the failed database for investigation,
restore the verified pre-deployment backup through the approved database
engine procedure, run integrity and foreign-key checks, then restart the
confirmed service. Compare protected-table counts and ID digests before
reopening access.

## Tested Evidence

On copied development databases:

- Reverse `0198` to `0197`: PASS
- Reapply `0198`: PASS
- Integrity check: `ok`
- Foreign-key check: PASS
- Protected count and primary-key digest mismatches: `0`
- Restore copied-development backup: PASS
- Restored checksum:
  `ac0c3e3eda99cce10d69a475fe87f71741a835bdd70d96bf6b8ceec2a7669073`
- Stage 11 rollback: `0.960s`
- Stage 11 reapply: `0.461s`
- Local copied-development restore: `0.003094s`

Production restore time is unknown until the sanitized production rehearsal.
