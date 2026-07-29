# KPI Production Checklist

Mark each item with owner, date, evidence, and PASS or FAIL.

## Release Identity

- [ ] Current production commit confirmed
- [ ] Stage 10 commit reviewed
- [ ] Release-candidate tag points to reviewed commit
- [ ] Worktree and deployment source are clean
- [ ] No private or database files are tracked

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
- [ ] Backup checksum verified
- [ ] Restore verified
- [ ] Forward migration passed
- [ ] Integrity and foreign keys passed
- [ ] Protected counts and ID digests matched
- [ ] Rollback owner and recovery time recorded

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

## Operations

- [ ] AWS host confirmed
- [ ] Project directory confirmed
- [ ] Service name confirmed
- [ ] Deployment window selected
- [ ] Monitoring owner assigned
- [ ] Error alert path tested
- [ ] Scheduler instructions reviewed
- [ ] Live scheduler remains disabled until separate activation

## Current Decision

`NOT SAFE TO DEPLOY`: human UAT, recent sanitized production rehearsal,
production commit, CEO approval, monitoring ownership, release window, and
release-candidate tag are not yet complete.
