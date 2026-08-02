# Finance Operations Production Deployment Checklist

Date: 2026-08-02

## Verified target

- [x] AWS instance: `i-03c61bae6189e94ce`, availability zone `us-east-1c`
- [x] SSH target: `ec2-user@femline.ca`
- [x] Production project: `/home/ec2-user/iconic_portal`
- [x] Production branch before release: `codex/financial-foundation-phase3a-production`
- [x] Production commit before release: `204fcda1d9f2cbaf0e08ff44d34bd9e0a2f1676e`
- [x] Database: SQLite, `/home/ec2-user/iconic_portal/db.sqlite3`
- [x] Application service: `gunicorn.service`
- [x] Web service: `nginx.service`
- [x] Backup root: `/home/ec2-user/backups`
- [x] Production working tree has only known untracked runtime/audit artifacts; no tracked changes
- [x] `FINANCIAL_CORE_WRITES_ENABLED=False`
- [x] `FINANCIAL_CORE_REPORTING_ACTIVE=False`

## Release scope

- [x] Finance Operations Center and 27 daily workflow types
- [x] Approval Center, Today's Activity, evidence authorization, and posting preview
- [x] Finance Operations object permissions and independent high-risk approval
- [x] Quick Costing factory timeline snapshot fields and calculations
- [x] Additive migration `0197_finance_operations_layer`
- [x] Required dormant dependency migrations `0192` through `0196`
- [x] No historical population command, exception import, Core bootstrap, recurring generation, or reconciliation command
- [x] No Financial Core write/reporting activation
- [x] No Chart of Accounts, formula, journal, or historical-data modification

Production currently ends at migration `0191`. The Operations code imports the dormant ledger, currency, payable, expense, payroll, production, permission, and preview services introduced by `0192` through `0196`; those additive schemas must accompany `0197`. They remain dormant because both activation flags stay off.

## Pre-deployment gates

- [ ] Create a mode-700 backup directory under `/home/ec2-user/backups`
- [ ] SQLite `.backup` completes and the backup integrity check returns `ok`
- [ ] Live and backup database SHA-256 values match at the backup checkpoint
- [ ] Media archive is readable
- [ ] `.env` backup retains mode `600` and its SHA-256 matches
- [ ] Source snapshot, Git metadata, service metadata, and Python package inventory are readable
- [ ] Record the exact rollback commit and backup path
- [ ] Push one reviewed release commit; do not deploy the dirty local `main` worktree

## Deployment gates

- [ ] Fetch and check out the exact reviewed release commit
- [ ] Confirm both Financial Core flags are false before migration
- [ ] Review the migration plan; only `0192` through `0197` may be pending
- [ ] Apply migrations without running population/bootstrap/reconciliation commands
- [ ] Run `manage.py check`, migration drift check, and focused Operations tests
- [ ] Restart only `gunicorn.service`; do not restart Nginx unless independently required
- [ ] Confirm Gunicorn and Nginx are active

## Post-deployment verification

- [ ] Public login page returns HTTP 200
- [ ] Operations routes require authentication
- [ ] Authorized desktop/mobile route smoke checks pass
- [ ] Filters, pagination, evidence authorization, approvals, rejection, and preview tests pass
- [ ] CEO, Super Admin, Finance, Accounts, Director, Manager, Production, Sales, and HR role tests pass
- [ ] Quick Costing timeline calculation tests pass without formula changes
- [ ] Query counts remain bounded and the Executive Dashboard budget remains unchanged
- [ ] Gunicorn log scan has no new traceback/template/500 errors
- [ ] Nginx access scan has no deployment-window 5xx errors
- [ ] Both Financial Core flags are false after service restart
- [ ] No Financial Core journal or historical correction was posted

## Stop conditions

Stop and roll back the application if the backup cannot be verified, migration plan contains an unexpected migration, either activation flag is true, tests fail, service health fails, or the deployed commit differs from the reviewed release commit.
