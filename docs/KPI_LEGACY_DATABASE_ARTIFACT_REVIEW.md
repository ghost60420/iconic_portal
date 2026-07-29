# KPI Legacy Database Artifact Review

## Decision

All 13 artifacts were added only in initial commit
`e46e03638cdcb7ef18773d5f003b78570d119aad` on 2026-01-03. No application,
settings, script, or test references any artifact; Django uses only
`db.sqlite3`.

Verified copies are stored outside the repository under owner-only
`$HOME/CRM Production Backups/kpi-legacy-sqlite-artifacts-20260729/`.
The 13 checksum entries pass. The backup directory is mode `700` and the files
are mode `600`.

Each artifact remains present in the local working directory but is removed
from future Git tracking on `chore/kpi-final-release-gates`. No Git history was
rewritten and no working database file was deleted.

## Artifact Matrix

All paths use ignore rules `db.sqlite3*`, `*.sqlite3`, `*.sqlite3.*`,
`*.sqlite3-wal`, and `*.sqlite3-shm`.

| Path | Size | Private data | Production dependency | Future tracking | Backup location |
| --- | ---: | --- | --- | --- | --- |
| `db.sqlite3.TESTSAVE` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db.sqlite3.backup` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db.sqlite3.backup.safe_copy_1` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db.sqlite3.before_user_reset` | 843,776 | Yes, 3 user records | None | Removed | External folder, same name |
| `db.sqlite3.before_user_wipe` | 843,776 | Yes, 3 user records | None | Removed | External folder, same name |
| `db.sqlite3.right_now_backup` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db.sqlite3.safe_copy` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db.sqlite3.safe_copy_1` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db_backup_before_ai_fix.safe_copy_1.sqlite3` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db_backup_before_ai_fix.sqlite3` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db_empty_copy.sqlite3` | 843,776 | Yes, 1 user record | None | Removed | External folder, same name |
| `db_recovered.sqlite3` | 778,240 | No business/user rows | None | Removed | External folder, same name |
| `db_try.sqlite3` | 778,240 | No business/user rows | None | Removed | External folder, same name |

All 13 pass SQLite integrity checks. The database files contain between 60 and
64 tables and historical migration metadata through CRM migrations `0067`,
`0069`, or `0077`. No lead, invoice, message, WhatsApp, file, or opportunity
file records were present.

## Checksums

| Path | SHA-256 |
| --- | --- |
| `db.sqlite3.TESTSAVE` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |
| `db.sqlite3.backup` | `0bb492441354d9ab8df98d40700ea064818e94be919d2a43c9dcc9cf44283b31` |
| `db.sqlite3.backup.safe_copy_1` | `0bb492441354d9ab8df98d40700ea064818e94be919d2a43c9dcc9cf44283b31` |
| `db.sqlite3.before_user_reset` | `80d0870d9cc4c53f4de71eaab9414e6c033bd66b2a085c4a2ad27019930ad01d` |
| `db.sqlite3.before_user_wipe` | `691cc156168d3b92e18525ee7e5b8cad5c0b456cd85e94226b9d1d15ba540995` |
| `db.sqlite3.right_now_backup` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |
| `db.sqlite3.safe_copy` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |
| `db.sqlite3.safe_copy_1` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |
| `db_backup_before_ai_fix.safe_copy_1.sqlite3` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |
| `db_backup_before_ai_fix.sqlite3` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |
| `db_empty_copy.sqlite3` | `49136bccca68c3c1a28523fa66afc25c619dd68ca7b7ea7339bd03c21177b299` |
| `db_recovered.sqlite3` | `5347fc19361b25329fba2dd75276240acb7deac7dcbd3189e37e493c70962e64` |
| `db_try.sqlite3` | `f4674453c8ea3b194ba1a2767e47adec11cd8c0b1037adb28dcb9f57f6afdea2` |

## Residual Security Risk

The initial Git history still contains all blobs, including three files with
user password hashes and identity fields. History rewriting was explicitly
prohibited. Access to the repository history must remain restricted, and any
credential/privacy response must be separately approved.
