# Financial Foundation Phase 3C: Historical Receivables Ledger

## Status

- Scope: historical receivables population and reconciliation only
- Production deployment: not performed
- Production migrations: not performed
- Production financial records changed: no
- Dashboard, reports, APIs, permissions, and active workflows changed: no
- Recommendation: NOT READY FOR PHASE 3C DEPLOYMENT

## Purpose

Phase 3C builds an append-only receivables history from existing Invoice,
InvoicePayment, and AccountingEntry evidence. It never updates or deletes a
source invoice, payment, or accounting entry. A dry run is the default and
performs no ledger writes.

## Canonical Historical Sources

### Invoice issued

An invoice principal is eligible when all of these conditions are true:

- `Invoice.status` is `sent`, `partial`, or `paid`;
- a customer is linked;
- `total_amount` is positive;
- currency is CAD, USD, or BDT.

The event amount is the immutable native `Invoice.total_amount`, and the event
date is `Invoice.effective_invoice_date`. Draft invoices are not treated as
issued. Cancelled invoices require original issuance and void evidence before
principal or reversal events can be built.

Invoices do not preserve historical exchange rates. Native currency remains
authoritative; unavailable converted snapshots are zero and reported as an
exception rather than filled with a current or estimated rate.

### Payment received

A receipt is eligible only when one InvoicePayment links to one matching
AccountingEntry and all of these values agree:

- invoice customer;
- native amount and currency;
- payment/accounting date;
- IN direction;
- non-cancelled and non-void accounting status;
- CAD and BDT rates and converted amounts.

One AccountingEntry cannot support more than one InvoicePayment. Shared or
mismatched records remain exceptions and do not produce ledger receipts.

### Allocation

Each eligible payment produces one invoice allocation in the invoice currency
and on the payment date. The allocation is capped at the invoice principal.
Any excess remains unallocated and requires Finance to classify it as customer
credit, duplicate receipt, refund, or reversal.

### Unsupported historical event sources

There is no canonical historical source for credit notes, refunds, reversals,
or opening receipts. Phase 3C does not infer them from notes, status text, or a
paid-amount difference. An opening receipt requires an approved evidence
manifest that identifies the bank/cash record and effective date.

## Reconciliation Rules

Current outstanding uses the existing Accounting dashboard scope without
changing its formula: non-archived invoices, excluding `paid` and `cancelled`,
with a positive `total_amount - paid_amount` balance.

Reconciliation is performed separately by native currency:

- ledger outstanding = posted invoice principal - posted allocations;
- ledger payments = posted cash receipt events;
- recorded payments = InvoicePayment amounts;
- accounting receipts = unique linked AccountingEntry native amounts.

No cross-currency totals are added together. A zero difference is required in
all three comparisons for that currency to reconcile.

## Write Controls

- Stable idempotency keys bind each event/allocation to its source primary key.
- A second run reuses exact matches and creates no duplicates.
- A conflicting existing ledger row aborts the transaction.
- Posted records include actor, approval time, evidence reference, and batch.
- Inserts are batched inside one atomic transaction.
- Apply mode requires an authenticated existing user.
- Apply mode refuses blocking exceptions by default.
- `--allow-exceptions` exists only for an isolated rehearsal of the safe subset.

The approved command is:

```bash
python manage.py populate_historical_receivables \
  --json-output /secure/path/phase3c.json \
  --markdown-output /secure/path/phase3c.md
```

No production apply command is approved while this document says NOT READY.

## Additive Schema

Migration `0193_phase3c_invoice_event_source` adds:

- `INVOICE_ISSUED` to ReceivableEvent event choices;
- nullable, protected `ReceivableEvent.source_invoice` provenance.

It contains no data migration, source-record update, or delete. Phase 3B
migration `0192` must precede it because Phase 3B is not yet deployed.

## Production-Copy Rehearsal

A SQLite online backup of the live database was copied to an isolated local
worktree. The remote temporary backup was removed after its SHA-256 matched the
local copy. Production stayed on Phase 3A with Gunicorn active.

Rehearsal result:

- invoices scanned: 41;
- payments scanned: 25;
- invoice-issued events created: 27;
- cash-receipt events created: 24;
- allocations created: 23;
- blocking exceptions: 28 across 23 invoices;
- advisory missing-FX exceptions: 9;
- second-run events created: 0, reused: 51;
- second-run allocations created: 0, reused: 23;
- SQLite integrity check: `ok`;
- foreign-key violations: 0.

The source-table dumps for Invoice, InvoicePayment, and AccountingEntry had
identical SHA-256 hashes before and after population. The detailed row-level
reports are intentionally stored outside Git under
`/tmp/iconic_phase3c_reports/` because they contain customer financial data.

## Rehearsal Differences

| Currency | Current outstanding | Ledger outstanding | Difference | Recorded payments | Ledger payments | Payment difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BDT | 974,934.00 | 560,074.00 | -414,860.00 | 46,802.00 | 45,002.00 | -1,800.00 |
| CAD | 17,106.25 | 21,152.25 | 4,046.00 | 42,122.66 | 42,122.66 | 0.00 |
| USD | 350.00 | 350.00 | 0.00 | 0.00 | 0.00 | 0.00 |

The BDT Accounting receipt difference is also -1,800.00. These differences
must be resolved with source evidence; the importer must not manufacture
opening receipts, refunds, reversals, or invoice issuance.

## Performance

- dry-run planner: 2 queries;
- tested apply batch (20 invoices, 40 events, 20 allocations): 14 queries;
- N+1 verification: passed; inserts and source reads are batched;
- measured production-copy dry-run process time: 1.24 s cold, 0.86 s warm;
- measured in-process planner time: 0.012690 s first, 0.005414 s warm;
- dashboard query path: unchanged and still covered by its 10-query bound;
- dashboard response code and template path: unchanged.

This is an offline maintenance operation. A future approved production run
must stop application writes, take and verify a fresh database backup, execute
the dry run again, and compare its signed exception report before any apply.

## Rollback

Before population, migrations `0193` and `0192` can be reversed only while the
ledger tables remain empty. After any ledger event exists, do not delete or
reverse migration history. During an approved maintenance window, a failed
population must restore the exact pre-run SQLite backup before application
services resume. Existing source records are never repaired by this command.
