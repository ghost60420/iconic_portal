# Phase A Rollback Instructions

## Scope

Phase A is a frontend-only Sales UI deployment. It should not run migrations or change database records.

Rollback should restore code and collected static assets only, unless a separate data-changing issue is discovered.

## Pre-Rollback Inputs

Record before rollback:

- Current branch
- Current commit
- Current `git status --short`
- Gunicorn status
- Login page HTTP status

## Code Rollback

Use the last known good production commit recorded before deployment.

```bash
cd /home/ec2-user/iconic_portal
git checkout <previous_production_commit>
python3 manage.py collectstatic --noinput
sudo systemctl restart gunicorn.service
```

Do not restore the database for a CSS/template issue.

## Staticfiles Rollback

If a staticfiles backup was created before deployment:

```bash
cd /home/ec2-user/iconic_portal
rm -rf staticfiles
cp -a /home/ec2-user/backups/<backup_folder>/staticfiles ./staticfiles
sudo systemctl restart gunicorn.service
```

## Database Rollback

Database restore should be used only if verified data changed unexpectedly. This Phase A release should not modify data.

```bash
cd /home/ec2-user/iconic_portal
cp /home/ec2-user/backups/<backup_folder>/db.sqlite3 ./db.sqlite3
sudo systemctl restart gunicorn.service
```

## Verification After Rollback

```bash
curl -I https://femline.ca/accounts/login/
sudo systemctl status gunicorn.service --no-pager
journalctl -u gunicorn.service -n 100 --no-pager
```

Verify these pages manually:

- Login
- Main Dashboard
- Leads List
- Lead Detail
- Opportunities List
- Opportunity Detail
- Customers List
- Customer Detail
- Quick Costing List
- Quick Costing Detail
- CEO Quotation Approval Queue

## Rollback Stop Condition

Stop after rollback verification passes. Do not attempt further fixes on production without a new reviewed patch.
