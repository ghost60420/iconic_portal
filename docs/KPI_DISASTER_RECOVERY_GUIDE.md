# KPI Disaster Recovery Guide

## Recovery Objectives

The release owner must record approved RTO and RPO values before deployment.
Stage 11 local timings are not production recovery objectives.

## Protected Assets

Back up and verify:

1. the production database using its confirmed engine-native process;
2. uploaded evidence and media;
3. the exact application commit and release tag;
4. environment and scheduler configuration through the approved secret store;
5. monitoring and deployment records.

Never commit database copies, media backups, secrets, or private inventories to
Git.

## Recovery Sequence

1. Stop writes and disable only approved KPI schedules.
2. Record the failing commit, database state, service state, and time.
3. Preserve the failed database for investigation.
4. Restore the verified pre-release database backup.
5. Restore media or object-storage versions.
6. check database integrity and foreign keys.
7. compare protected table counts and primary-key digests.
8. start the previous confirmed application commit.
9. verify login, protected CRM modules, KPI pages, snapshots, exports, and
   notifications.
10. reopen access only after the recovery owner approves.

## Validation

The copied development database restored to checksum
`ac0c3e3eda99cce10d69a475fe87f71741a835bdd70d96bf6b8ceec2a7669073`
in the Stage 11 local test. This proves the local method only. A recent
sanitized production-size backup and restore remains required.

## Stop Conditions

Do not continue when IDs, counts, relationships, snapshots, files, financial
records, employee records, or permissions differ. Do not fake migration state,
delete ledger entries, or reverse additive KPI schema after live KPI data
exists without a verified full restore plan.
