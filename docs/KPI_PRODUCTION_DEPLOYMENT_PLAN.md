# KPI Production Deployment Plan

## Status

This is a preparation plan. It has not been executed. AWS host, project
directory, service name, production database path, current production commit,
deployment window, and responsible operators are not confirmed and must not be
guessed.

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
