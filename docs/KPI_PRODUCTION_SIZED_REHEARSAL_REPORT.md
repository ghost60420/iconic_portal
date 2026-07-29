# KPI Production-Sized Rehearsal Report

## Result

`NOT TESTED`

No approved recent sanitized production database copy was available. The live
database was not migrated or used as a rehearsal database.

## Candidate Review

A local file named
`db.sqlite3.production_fresh_20260725_160955` was inspected only for metadata:

- Size: `118,665,216` bytes.
- SHA-256:
  `9de13359158ebddfd1b3f30b610ee014fc0ce35665df4fada7a441934594232b`.
- Integrity: `ok`.
- Latest CRM migration: `0187_product_reference_images_six_slots`.
- User rows: `10`.
- Sanitization evidence: none.

Because it contains production user records and no sanitization manifest, it
was rejected for Stage 11 rehearsal. It was not copied, migrated, or committed
by this task.

## Required Rehearsal Results

| Measure | Result |
| --- | --- |
| Sanitized database size | NOT TESTED |
| Record totals | NOT TESTED |
| Backup and checksum | NOT TESTED |
| Forward migrations through `0198` | NOT TESTED |
| Data integrity and foreign keys | NOT TESTED |
| Record counts and primary keys | NOT TESTED |
| KPI page loading | NOT TESTED |
| Dashboard and Intelligence loading | NOT TESTED |
| Notification checks | NOT TESTED |
| Export generation | NOT TESTED |
| Database rollback | NOT TESTED |
| Application rollback | NOT TESTED |
| Restore and reapply | NOT TESTED |
| Migration duration | NOT TESTED |
| Rollback duration | NOT TESTED |
| Restore duration | NOT TESTED |
| Estimated downtime | NOT TESTED |
| Peak memory | NOT TESTED |

## Supporting Non-Production Evidence

These checks pass but do not close the production-sized gate:

- Fresh empty migration through `crm.0198`: `49.920s`.
- Fresh rollback `0198` to `0197`: `0.707s`.
- Fresh reapply: `0.380s`.
- Repaired populated development copy `0191` through `0198`: `7.040s`.
- Populated development rollback/reapply: `0.742s` / `0.305s`.
- Pre-existing populated business tables checked: `173`.
- Changed count or primary-key fingerprints: `0`.
- Integrity: `ok`; foreign-key violations: `0`.
- Live online backup/isolated restore: `294ms` / `263ms`.

## Disk Capacity

The minimum local allowance uses the measured `119,758,848`-byte live
database:

| Component | Bytes |
| --- | ---: |
| Current, backup, and restore database copies | 359,276,544 |
| Migration temporary and rollback copies | 239,517,696 |
| Current production static files | 14,885,867 |
| Current production application logs | 288,674 |
| Application rollback checkout | 77,672,448 |
| Safety margin equal to two database copies | 239,517,696 |
| **Required** | **931,158,925** |

- Local available before gate work: `1,580,085,248` bytes.
- Local available after isolated tests: `1,582,448,640` bytes.
- Calculated remaining headroom: approximately `649 MB`.
- Production available before backup/restore test: `15,955,341,312` bytes.
- Production backup/restore peak used: `239,575,040` bytes.
- Production available after: `15,715,766,272` bytes.

Capacity meets the calculated single-rehearsal minimum. The local APFS volume
still reports `100%` rounded utilization, so no extra production copies should
be created without rechecking free space.

## Exit Requirement

Obtain an approved, current, sanitized copy with a sanitization manifest and
authorized custodian. Repeat every required result above, record peak
resources and timing, and preserve no private copy in Git.
