# Pre-KPI Reconciliation Rollback Plan

## Current Safety Position

- Original worktree remains on dirty local `main`.
- Original commit is protected by
  `backup/pre-kpi-reconciliation-20260728`.
- Original tracked/source changes are protected by
  `<original-worktree>/backups/pre_kpi_reconciliation.patch`.
- All original untracked files are protected by a verified SHA-256 manifest.
- Original development database was not changed.
- Production and AWS were not accessed.
- Reconciliation work is isolated in
  a dedicated `chore/pre-kpi-reconciliation` worktree.

## Tested Recovery Controls

### PASS - patch verification

```text
git apply --check --reverse backups/pre_kpi_reconciliation.patch
```

The command passed against the captured original working tree.

### PASS - untracked verification

```text
shasum -a 256 -c backups/pre_kpi_untracked_manifest.sha256
```

All 331 original untracked files passed.

### PASS - fresh schema rollback

On a fresh approved-schema test database:

1. CRM `0191` and `0190` were reversed to `0189`.
2. Seven Library permission columns were removed.
3. CRM `0190` and `0191` were reapplied.
4. All seven fields were restored.
5. SQLite integrity remained `ok`.
6. Foreign-key violations remained zero.

This proves schema rollback/reapply on an empty fresh database only.

### PASS - populated development rollback

The repaired populated copy was duplicated and rolled back:

1. CRM `0191` to `0141`.
2. Marketing `0012` to `0007`.
3. WhatsApp `0005` to `0004`.
4. All 56 entries were reapplied normally.
5. All table row counts matched the pre-rollback repaired copy.
6. All primary-key digests matched except `django_migrations`, whose applied
   timestamps are expected to change.
7. SQLite integrity remained `ok`; foreign-key violations remained zero.

The immutable original and initial rollback copies retain SHA-256
`985e31266de25561dc097745f569e5246de5e6a78bfb8a68b38811019e0569cd`.

## Restore the Original Source State

Do not modify the existing dirty worktree. Create a separate recovery worktree:

```bash
cd <original-worktree>
git worktree add ../iconic_portal_pre_kpi_recovery \
  backup/pre-kpi-reconciliation-20260728
cd ../iconic_portal_pre_kpi_recovery
git apply --binary \
  <original-worktree>/backups/pre_kpi_reconciliation.patch
git diff --check
git status --short
```

The patch restores all staged/modified tracked files and all original
non-SQLite untracked source/artifact files. It does not recreate the original
staging split. The exact 27 staged paths are recorded in
`backups/pre_kpi_staged_files.txt`.

Verify the original SQLite and untracked artifacts in place:

```bash
cd <original-worktree>
shasum -a 256 -c backups/pre_kpi_untracked_manifest.sha256
```

Do not delete, rename, or commit SQLite copies.

## Abandon the Reconciliation Candidate

No reset is required. The original worktree and branch were never switched.

1. Preserve these reports outside the worktree if required.
2. Confirm `chore/pre-kpi-reconciliation` has no commit needed elsewhere.
3. Stop using the sibling worktree.
4. Remove the worktree only with a normal `git worktree remove` after explicit
   approval and after all reports are preserved.
5. Keep the safety branch until KPI deployment is complete and separately
   approved.

Do not use `git reset --hard` and do not force push.

## Database Candidate Rollback

The safe development-database reconciliation method is a new candidate database,
not in-place mutation.

1. Keep `<original-worktree>/db.sqlite3` immutable.
2. Keep the SQLite online backup and its checksum.
3. Build a candidate from the approved migration graph.
4. Import data using an explicit reviewed mapping while preserving IDs.
5. If any migration, row-count, checksum, finance-total, foreign-key, permission,
   or regression check fails, stop the candidate.
6. Discard the candidate database.
7. Reopen the unchanged source development database for recovery/reference.

No source database restore command is required because the source must never be
overwritten during rehearsal.

## Future Populated Reconciliation Rollback

Before a populated transfer rehearsal:

1. Create a new SQLite online backup.
2. Record source/candidate hashes, integrity, ledger, schema, row counts, key
   financial totals, and protected identifiers.
3. Store the import mapping and dry-run report.
4. Run the transfer only against a new candidate file.
5. Run all migrations and regressions.
6. Test rollback by discarding the candidate and reopening the checksum-matched
   source copy.
7. Rebuild and reapply from the same source to prove repeatability.

Do not use `migrate --fake` unless every relevant schema operation and ledger
entry is proven identical. Current evidence proves they are not identical.

## Production Rollback

No production action is authorized in this stage. Before any future production
work, separately confirm:

- target/source branches and exact commits;
- AWS host and project directory;
- service name;
- production database engine and migration ledger;
- verified production backup and restore;
- production row/checksum baselines;
- exact rollback commands that do not discard uncommitted server work.

The destructive rollback command found in an older deployment report was not
run and is not approved by this plan.

## Rollback Status

- Source recovery controls: **PASS**
- Fresh empty-schema rollback/reapply: **PASS**
- Populated development migration rollback/reapply: **PASS**
- Production rollback: **NOT TESTED**

**DEVELOPMENT ROLLBACK PROVEN; PRODUCTION NOT TESTED**
