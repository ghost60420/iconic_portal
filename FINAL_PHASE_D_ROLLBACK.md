# Final Phase D Rollback

## Rollback Baseline

- Rollback branch: `codex/ui-density-phase3-enterprise`
- Rollback commit: `06d7888b5d70a3ea042174f45c87c4d3704c0612`
- Backup path: `/home/ec2-user/backups/phase_d_predeploy_20260714_031528`
- Database integrity at backup time: `ok`
- Database SHA256:
  - `e615c24c6349eb418b71e1fb4b87d05a372653fd3c098c4caeea4b41a65e5e7f`

## Standard UI Rollback

Use this when the issue is template, CSS, static asset, or layout related and no data was modified.

```bash
cd /home/ec2-user/iconic_portal

git checkout 06d7888b5d70a3ea042174f45c87c4d3704c0612
python3 manage.py collectstatic --noinput
sudo systemctl restart gunicorn.service

sudo systemctl is-active gunicorn.service
curl -I https://femline.ca/accounts/login/
```

Do not restore the database for a CSS/template issue. Restoring the database unnecessarily could remove live records created after the backup.

## Staticfiles Restore

Use if `collectstatic` leaves static files broken and code rollback alone is not enough.

```bash
cd /home/ec2-user/iconic_portal

mv staticfiles staticfiles.failed_phase_d_$(date +%Y%m%d_%H%M%S)
tar -xzf /home/ec2-user/backups/phase_d_predeploy_20260714_031528/staticfiles.tar.gz

sudo systemctl restart gunicorn.service
curl -I https://femline.ca/accounts/login/
```

## Database Restore

Use only if deployment or smoke testing confirms database data was modified or corrupted. This release is not expected to write data.

```bash
cd /home/ec2-user/iconic_portal

cp db.sqlite3 db.sqlite3.failed_phase_d_$(date +%Y%m%d_%H%M%S)
cp /home/ec2-user/backups/phase_d_predeploy_20260714_031528/db.sqlite3 db.sqlite3
sqlite3 db.sqlite3 "PRAGMA integrity_check;"

sudo systemctl restart gunicorn.service
curl -I https://femline.ca/accounts/login/
```

Expected SQLite result:

```text
ok
```

## Media Restore

Use only if media files are accidentally modified or missing.

```bash
cd /home/ec2-user/iconic_portal

mv media media.failed_phase_d_$(date +%Y%m%d_%H%M%S)
tar -xzf /home/ec2-user/backups/phase_d_predeploy_20260714_031528/media.tar.gz

sudo systemctl restart gunicorn.service
```

## Source Archive Restore

Use only if Git checkout is not enough or the project tree is damaged.

```bash
cd /home/ec2-user

mv iconic_portal iconic_portal.failed_phase_d_$(date +%Y%m%d_%H%M%S)
mkdir iconic_portal
cd iconic_portal
tar -xzf /home/ec2-user/backups/phase_d_predeploy_20260714_031528/project_source.tar.gz

sudo systemctl restart gunicorn.service
curl -I https://femline.ca/accounts/login/
```

## Post Rollback Verification

- Login page returns HTTP 200.
- Main Dashboard loads.
- Marketing Dashboard loads.
- AI Dashboard loads.
- CEO Dashboard loads.
- Email Center loads.
- WhatsApp Center loads.
- No new gunicorn traceback:

```bash
journalctl -u gunicorn.service -n 100 --no-pager
```

- No nginx 500/template/static errors:

```bash
sudo tail -100 /var/log/nginx/error.log
```

## Rollback Stop Conditions

Stop and escalate if:

- SQLite integrity check is not `ok`.
- Gunicorn does not restart.
- Login page returns 500.
- Restored source tree is missing expected files.
- Media restore would overwrite newer user uploads created after the backup.
