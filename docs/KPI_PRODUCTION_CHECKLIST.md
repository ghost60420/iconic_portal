# KPI Production Checklist

Mark each item with owner, date, evidence, and PASS or FAIL.

## Release Identity

- [ ] Current production commit confirmed
- [x] Stage 10 commit included as `0775935ab470deb0d8702e965d03d24e8fb65589`
- [ ] Release-candidate tag points to reviewed commit
- [ ] Worktree and deployment source are clean
- [ ] No private or database files are tracked

The current branch has only intended Stage 11 documentation changes plus two
pre-existing untracked historical reconciliation documents. Thirteen SQLite
backup artifacts from the initial commit remain tracked. This blocks release
until an approved security remediation is complete.

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

- [ ] AWS host confirmed
- [ ] Project directory confirmed
- [ ] Service name confirmed
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

`NOT SAFE FOR PRODUCTION DEPLOYMENT`: human UAT, recent sanitized
production-size rehearsal, production identity, legacy tracked database
artifact remediation, production security configuration, capacity,
monitoring ownership, CEO approval, deployment window, and release-candidate
tag are not complete.
