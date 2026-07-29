# KPI Production Environment Record

## Read-Only Verification

Verified on 2026-07-29 through read-only SSH commands. No code, service,
environment, database ledger, scheduler, or production record was changed.

| Item | Verified value |
| --- | --- |
| Public host | `femline.ca` |
| Internal host | `ip-172-31-16-247.ec2.internal` |
| Project path | `/home/ec2-user/iconic_portal` |
| Production branch | `living-catalog-production-deployment` |
| Production commit | `1084fc99be3fade235ca0bfa73673a3a44b7ff3e` |
| Application version | `ui-density-phase1-approved-46-g1084fc9` |
| Commit date | `2026-07-28T10:20:02-07:00` |
| Application service | `gunicorn.service` |
| Service active since | `2026-07-28 17:17:05 UTC` |
| Service bind | `127.0.0.1:8000` |
| Python environment | `/home/ec2-user/iconic_portal/venv/bin/python` |
| Python runtime | `3.11.14` |
| Database engine | SQLite |
| Database path | `/home/ec2-user/iconic_portal/db.sqlite3` |
| Database size at check | `119,758,848` bytes |
| Database integrity | `ok` |
| Current CRM migration | `0191_library_permission_controls` |
| Static path | `/home/ec2-user/iconic_portal/staticfiles` |
| Media path | `/home/ec2-user/iconic_portal/media` |
| Scheduler method | User crontab for existing non-KPI jobs |
| KPI scheduler | Not installed or active |

Nginx proxies the public application to port 8000. A separate legacy
`iconiccrm.service` remains active on port 8001; it is not the Nginx target and
must be reviewed by operations before release to avoid ambiguous service
ownership.

## Worktree

The production tracked worktree is clean. Untracked operational paths exist:

- `.reconcile_worktree/`
- `CRM_DATA_INTEGRITY_DETAILS.md`
- `OPPORTUNITY_STAGE_AUDIT_REPORT.md`
- `backups/`
- `crm_integrity_export.csv`
- `logs/`

These were not opened, changed, staged, or deleted during this task.

## Environment Metadata

The production `.env` is owner-only mode `600`. Of the release-health keys
checked, only `DJANGO_SECRET_KEY` is present. These values are not configured:

- `APP_VERSION`
- `GIT_COMMIT`
- `DEPLOYED_AT`
- `LAST_BACKUP_AT`
- KPI scheduler or monitoring variables

The application health page is authenticated and returns `302` to an
unauthenticated local request. It is not currently a public monitoring probe.

## Backup And Restore Evidence

An online SQLite backup and isolated restore test was completed without
altering the live database:

| Item | Result |
| --- | --- |
| Backup directory | `/home/ec2-user/backups/kpi_release_gate_backup_restore_20260729T202355Z` |
| Directory/file modes | `700` / `600` |
| Backup size | `119,758,848` bytes |
| Backup SHA-256 | `f79208fe024b913e251a525212ca7f37f594679483fa8ee7f85191220d5b2adb` |
| Backup time | `294 ms` |
| Restore time | `263 ms` |
| Backup/restore checksum | Exact match |
| Schema fingerprint | Exact match |
| Table-count manifest | Exact match |
| Integrity | `ok` on both files |
| Foreign-key violations | `0` on both files |
| Migration in backup | `crm.0191_library_permission_controls` |

Technical backup and restore are proven. Encryption, retention, off-host
replication, named owner, backup-failure alerting, and approved recovery-time
objective are not proven and remain release blockers.
