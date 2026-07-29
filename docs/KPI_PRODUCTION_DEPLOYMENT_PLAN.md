# KPI Production Deployment Plan

## Status

This is a preparation plan. It has not been executed. Read-only checks confirm
the host, project directory, service, database path, and production commit.
The deployment window and responsible operators remain unconfirmed and must
not be guessed.

Stage 11 decision: `NOT SAFE FOR PRODUCTION DEPLOYMENT`.

## Confirmed Read-Only Values

- Host: `ec2-user@femline.ca`
- Project directory: `/home/ec2-user/iconic_portal`
- Application service: `gunicorn.service`
- Database: SQLite at `/home/ec2-user/iconic_portal/db.sqlite3`
- Current production branch: `living-catalog-production-deployment`
- Current production commit:
  `1084fc99be3fade235ca0bfa73673a3a44b7ff3e`

These values are evidence, not deployment approval.

## Deployment Window Proposal

No window is selected or activated.

| Field | Proposed value |
| --- | --- |
| Date | TBD, requires approval |
| Start time | TBD, requires approval |
| Expected duration | NOT TESTED on production-sized sanitized data |
| Expected downtime | NOT TESTED |
| Deployment owner | Unassigned |
| Database owner | Unassigned |
| Test owner | Unassigned |
| Rollback owner | Unassigned |
| Monitoring owner | Unassigned |
| CEO approval | Not recorded |
| Team notification | Not prepared |
| Rollback decision deadline | TBD after rehearsal timing |

## Required Inputs

Record all values before approval:

- Release candidate tag and commit
- Production branch and current commit
- AWS host
- Project directory
- Application service name
- Production database engine and backup command
- Static-file command, if required
- Deployment window and rollback owner
- Monitoring and error-alert owner

## Preflight

```bash
git status --short --branch
git rev-parse HEAD
git show-ref --tags kpi-release-candidate-1
git remote -v
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py migrate --plan
```

Stop if the worktree is dirty, the commit/tag differs, the migration plan
contains an unexpected operation, or the production commit is not recorded.
Also stop when tracked database artifacts remain in the candidate tree, disk
headroom is below the approved migration and backup requirement, deploy checks
report unresolved security warnings, or health metadata and alert ownership
are unconfigured.

## Backup

Use the production database engine’s approved online backup process. For
SQLite only, after stopping writes and confirming `PRODUCTION_DB_PATH`:

```bash
test -n "$PRODUCTION_DB_PATH"
test -n "$BACKUP_PATH"
sqlite3 "$PRODUCTION_DB_PATH" ".backup '$BACKUP_PATH'"
shasum -a 256 "$PRODUCTION_DB_PATH" "$BACKUP_PATH"
sqlite3 "$BACKUP_PATH" "PRAGMA integrity_check;"
```

Do not assume production uses SQLite. Verify uploaded/media-file backup
separately and record checksums or object-storage version identifiers.

## Controlled Release Sequence

The exact `ssh`, `cd`, Git, service, and static commands must be filled with
confirmed values and approved in a separate deployment instruction:

```bash
ssh "$APPROVED_AWS_HOST"
cd "$APPROVED_PROJECT_DIRECTORY"
git status --short --branch
git rev-parse HEAD
git fetch origin --tags
git checkout "$APPROVED_RELEASE_TAG"
python3 manage.py check
python3 manage.py migrate --plan
python3 manage.py migrate
python3 manage.py collectstatic --noinput  # only if confirmed by current deploy method
sudo systemctl restart "$APPROVED_APP_SERVICE"
sudo systemctl status "$APPROVED_APP_SERVICE" --no-pager
```

Do not run `prepare_kpi_release` until application health is verified. Then
create drafts only with CEO-approved effective date and currency. Do not
publish policies or activate automation in the deployment window.

## Verification

Verify authenticated access and server authorization for:

1. Login and employee profile
2. Performance tab and review queue
3. Dashboard widgets
4. Executive Intelligence and all export formats
5. Notification Center
6. Direct URL denial for unauthorized employee, manager, HR, and Accounts users
7. Lead, opportunity, production, quotation, invoice, payment, marketing, and
   FedEx smoke workflows
8. Application logs, migration ledger, database integrity, and error alerts

## Monitoring

For at least one full business day, monitor HTTP errors, application service
status, migration/database errors, permission denials, KPI audit events,
notification automation failures, query latency, and queue growth. Keep KPI
notification schedules disabled until the published policy and scheduler
activation receive separate approval.

## Stop Conditions

Stop and roll back for lost records, changed IDs, foreign-key violations,
permission exposure, bonus exposure, unexpected notifications, broken
protected workflows, failed health checks, or materially increased query
counts.

## Stage 11 Local Evidence

- Fresh migration through `crm.0198`: 55.954s.
- Copied-development migration `0191` through `0198`: 6.704s.
- Protected count and primary-key digest mismatches: 0.
- SQLite integrity: `ok`; foreign-key violations: 0.
- Rollback `0198` to `0197`: 0.960s; reapply: 0.461s.
- Exact-checksum copied-development restore: 0.003094s.

These timings do not estimate production downtime. A recent sanitized
production-size rehearsal is still required.

## Final Gate Closure Evidence

- Full CRM suite: 969 of 969 passed.
- KPI suite: 206 of 206 passed.
- Fresh migration/rollback/reapply: 49.920s / 0.707s / 0.380s.
- Populated development migration/rollback/reapply:
  7.040s / 0.742s / 0.305s.
- Production online backup/isolated restore: 294ms / 263ms.
- Exact production commit and environment paths: confirmed read-only.
- Human UAT, sanitized production-sized rehearsal, named owners, CEO approval,
  security environment activation, monitoring alerts, and deployment window:
  incomplete.
