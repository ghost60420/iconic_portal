# Financial Deployment And Rollback

## Current decision

Do not deploy or activate. The database-copy rehearsal has unresolved Critical exceptions. This document is a reviewed release procedure for a later CEO-approved window, not authorization to run it.

## Release boundaries

Use separate releases:

1. Schema release: additive migrations with both feature flags off.
2. Configuration release: Chart of Accounts and approved periods/accounts.
3. Historical rehearsal/approval: database copy only; no production repair.
4. Evidence-approved historical posting: separately reviewed manifest and backup.
5. Controlled dual-write activation.
6. Reporting activation only after control reconciliation.

Historical repair must never share a release with schema deployment.

## Required preflight

Record the actual target branch, deploy source branch, AWS host, project directory, service name, database engine, backup path, current commit, remotes, status, and changed files. Stop if any value is unknown. Obtain CEO approval and a Finance-approved exception manifest.

Run in the verified project directory with its verified virtual environment:

```bash
git status --short
git branch --show-current
git log -1 --oneline
git remote -v
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py showmigrations crm
```

Create and verify a database backup using the database engine's approved production procedure before migration. The current repository uses SQLite locally, but production storage must be discovered rather than assumed.

## Schema release

With `FINANCIAL_CORE_WRITES_ENABLED=False` and `FINANCIAL_CORE_REPORTING_ACTIVE=False`:

```bash
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py check
python manage.py showmigrations crm
```

Run `bootstrap_financial_core --actor <approved-existing-username>` first without `--apply`. Review the counts, then run it with `--apply` only after approval. Configure financial periods and named instruments through approved records; do not fabricate opening balances.

Migration `0196_financial_readiness` is additive. It creates only the exception review and adjustment-request structures and their indexes/constraints. It does not import exceptions or post an opening balance. Run `load_financial_exceptions` only against a copied database until a separate CEO-approved historical review release.

Migration `0197_finance_operations_layer` is also additive. It creates the `FinanceOperation` staging/orchestration table and its constraints, indexes, and relationships. It does not create a journal, change an existing transaction, or enable either Financial Core flag.

## Safe activation order

1. Record branch, commit, remote, target host, project folder, service, database engine, and current flags without guessing.
2. Verify the production backup and a restore rehearsal.
3. Deploy the additive schema/code with both Financial Core flags off.
4. Verify migration `0196`, application checks, protected-media routing, permissions, and existing CRM smoke tests.
5. Import the approved exception manifest in an isolated copy and obtain Finance/CEO evidence decisions.
6. Post only the separately approved, balanced opening/adjustment manifest in a controlled rehearsal.
7. Reconcile Trial Balance, Balance Sheet, P&L, Cash Flow, AR, AP, bank/cash, and currency exposure.
8. Repeat backup verification and obtain written CEO activation approval.
9. Enable `FINANCIAL_CORE_WRITES_ENABLED` first for the approved controlled cohort; keep reporting activation off.
10. Verify dual-write/reconciliation and monitor application/database errors.
11. Enable `FINANCIAL_CORE_REPORTING_ACTIVE` only after reports match the General Ledger and control accounts.
12. Retain release evidence and monitor the first close/reconciliation cycle.

## Verification

- Existing lead, opportunity, quotation, invoice, and payment paths load and retain their current behavior while flags are off.
- Phase 3A, 3B, and 3C regression tests pass.
- Preview reports render but remain labelled unreconciled.
- Database integrity and application logs show no migration errors.
- No historical journal, bill, rate, or opening balance exists without evidence.

## Application rollback

The preferred rollback after any data exists is application rollback: turn both Financial Core flags off and redeploy the prior approved application commit. Do not reverse migrations containing posted financial records. The additive tables can remain dormant without affecting legacy reports.

## Schema rollback

For the Finance Operations schema alone, rollback to `0196` only if `crm_financeoperation` contains no records and no later migration depends on it:

```bash
python manage.py migrate crm 0196 --plan
python manage.py migrate crm 0196 --noinput
python manage.py check
```

Once any Finance Operations request exists, do not reverse `0197`. Keep both flags off, roll back the application to the prior approved commit, and preserve the additive table for audit/history. Reversal was rehearsed only on an empty copied database and was successfully reapplied.

For the readiness schema alone, rollback to `0195` only if the new review/adjustment tables are empty and no release depends on them:

```bash
python manage.py migrate crm 0195 --plan
python manage.py migrate crm 0195 --noinput
python manage.py check
```

If exception reviews, evidence links, or adjustment requests exist, do not reverse `0196`; disable both flags and roll back application code while preserving the additive tables.

Full Financial Core schema rollback is permitted only before any Financial Core configuration or transaction is created and after a verified backup. Confirm all tables introduced by `0194` through `0196` are empty. Then:

```bash
python manage.py migrate crm 0193 --plan
python manage.py migrate crm 0193 --noinput
python manage.py check
```

This removes `0195` and `0194`. Restore the verified pre-migration database backup instead if any new Financial Core data exists or if emptiness cannot be proven. Never use schema reversal to delete financial history.

## Protected document routing

Before activation, verify the production web server denies direct public access to `/media/financial_core/`, `/media/accounting/`, and `/media/accounting_docs/`. Templates use authorized application download endpoints, but this repository does not contain the Nginx configuration needed to prove the infrastructure rule.

## Historical population rollback

Phase 3C events are immutable evidence records. A production population must use a reviewed manifest and stable batch/source keys. If an approved historical posting is wrong, preserve it and create explicit reversal/adjustment records. Do not delete rows or restore a backup over newer valid financial activity.

## Recovery evidence

Retain migration output, backup verification, deployed commit, configuration command results, test output, query/timing measurements, exception manifest approval, service restart output, smoke-test screenshots, and monitoring evidence with the release record.
