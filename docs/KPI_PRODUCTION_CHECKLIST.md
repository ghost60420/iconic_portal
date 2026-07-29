# KPI Production Checklist

Mark each item with owner, date, evidence, and PASS or FAIL.

## Release Identity

- [x] Current production commit confirmed as `1084fc99be3fade235ca0bfa73673a3a44b7ff3e`
- [x] Stage 10 commit included as `0775935ab470deb0d8702e965d03d24e8fb65589`
- [ ] Release-candidate tag points to reviewed commit
- [ ] Worktree and deployment source are clean
- [x] No private or database files remain in the candidate index

The 13 initial-commit SQLite artifacts have verified owner-only external
copies, remain preserved locally, and are removed from future tracking. Git
history was not rewritten. Two pre-existing untracked historical
reconciliation documents remain untouched.

## Policies

- [ ] All 15 template drafts reviewed
- [ ] Status policy reviewed
- [ ] Bonus policy reviewed without unapproved money values
- [ ] Intelligence policy reviewed
- [ ] Notification and escalation policy reviewed
- [ ] CEO approval recorded
- [ ] Required policies approved and versions recorded
- [ ] Publication order approved

## Database

- [ ] Recent sanitized production copy available
- [ ] Production-size rehearsal passed
- [x] Live SQLite online backup and isolated restore mechanism verified
- [x] Copied-development backup checksum verified
- [x] Copied-development restore verified
- [x] Fresh and copied-development forward migration passed
- [x] Copied-development integrity and foreign keys passed
- [x] Copied-development protected counts and ID digests matched
- [ ] Rollback owner and recovery time recorded

Local evidence does not satisfy the production-size rehearsal gate.

## Security And UAT

- [ ] Employee UAT passed
- [ ] Three-role employee UAT passed
- [ ] Manager UAT passed
- [ ] Director UAT passed
- [ ] HR UAT passed without bonus money
- [ ] Accounts UAT passed without KPI edit authority
- [ ] CEO UAT passed
- [ ] Super Admin UAT passed
- [ ] Exports and notification actions rechecked server scope
- [ ] No public route or guessed-ID access

Automated synthetic browser scope, guessed-ID denial, exports, and responsive
checks passed. The unchecked UAT rows require named human testers.

## Operations

- [x] AWS host confirmed as `ec2-user@femline.ca`
- [x] Project directory confirmed as `/home/ec2-user/iconic_portal`
- [x] Service name confirmed as `gunicorn.service`
- [ ] Deployment window selected
- [ ] Monitoring owner assigned
- [ ] Error alert path tested
- [ ] Scheduler instructions reviewed
- [ ] Live scheduler remains disabled until separate activation
- [ ] `APP_VERSION` or `GIT_COMMIT` health metadata configured
- [ ] `LAST_BACKUP_AT` and `DEPLOYED_AT` health metadata configured
- [ ] Production SSL/HSTS/secure-cookie checks passed
- [ ] Production disk headroom meets the approved threshold

## Current Decision

`NOT SAFE FOR PRODUCTION DEPLOYMENT`: human UAT, a recent sanitized
production-size rehearsal, production security environment activation,
monitoring ownership and alert tests, backup encryption/retention ownership,
CEO approval, deployment window, production rollback timing, and the
release-candidate tag are not complete.
