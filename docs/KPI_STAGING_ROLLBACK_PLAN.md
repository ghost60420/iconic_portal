# KPI Private Staging Rollback Plan

This plan affects only `/home/ec2-user/iconic_kpi_staging` and
`iconic-kpi-staging.service`. It contains no production rollback action.

## Application Rollback

```bash
cd /home/ec2-user/iconic_kpi_staging
sudo systemctl stop iconic-kpi-staging.service
git fetch origin
git switch --detach APPROVED_STAGING_COMMIT
sudo systemctl start iconic-kpi-staging.service
sudo systemctl is-active iconic-kpi-staging.service
```

Use only a recorded staging commit. Do not substitute `main` or a production
branch.

## Database Rollback

```bash
cd /home/ec2-user/iconic_kpi_staging
sudo systemctl stop iconic-kpi-staging.service
cp -p var/db/staging.sqlite3 var/backups/pre_restore_staging.sqlite3
cp -p var/backups/post_uat_20260730T014300Z/staging.sqlite3 \
  var/db/staging.sqlite3
sqlite3 var/db/staging.sqlite3 "PRAGMA integrity_check;"
sqlite3 var/db/staging.sqlite3 "PRAGMA foreign_key_check;"
sudo systemctl start iconic-kpi-staging.service
```

## Settings Rollback

Restore only the owner-approved staging environment backup to
`config/staging.env`, keep mode `0600`, run `manage.py check --deploy`, then
restart only `iconic-kpi-staging.service`.

## Static Rollback

```bash
cd /home/ec2-user/iconic_kpi_staging
set -a
source config/staging.env
set +a
venv/bin/python manage.py collectstatic --noinput --clear
find var/static -type d -exec chmod 755 {} +
find var/static -type f -exec chmod 644 {} +
sudo nginx -t
sudo systemctl reload nginx.service
```

## Service Recovery

```bash
sudo systemctl daemon-reload
sudo systemctl restart iconic-kpi-staging.service
sudo systemctl status iconic-kpi-staging.service --no-pager
curl -I https://kpi-staging.100-51-11-48.sslip.io/
```

An unauthenticated final request must return `401`.

## Full Staging Removal

1. Stop and disable only `iconic-kpi-staging.service`.
2. Preserve the staging source/database/settings/static/media/log backups.
3. Remove only `/etc/nginx/conf.d/iconic-kpi-staging.conf`.
4. Run `sudo nginx -t`.
5. Reload nginx.
6. Verify production remains active.

Do not stop, restart, restore, or modify `gunicorn.service` or the production
database.
