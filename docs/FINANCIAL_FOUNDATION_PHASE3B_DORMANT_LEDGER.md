# Financial Foundation Phase 3B: Dormant Receivables Ledger

## Status

- Implementation scope: additive schema and isolated ledger service only
- Active invoice/payment reads changed: no
- Active invoice/payment writes changed: no
- Historical records changed: no
- Production data repair: not included
- Deployment status: not deployed; CEO approval is required after verification

## Purpose

Phase 3B creates the empty accounting foundation needed for evidence-backed
receivable events and invoice allocations. It does not make the new ledger
authoritative and does not dual-write current payments. Phase 3A remains the
only active invoice and payment write path.

## Additive Models

### ReceivableEvent

An append-only financial fact with:

- customer ownership;
- event type: cash receipt, credit note, refund, opening receipt, adjustment,
  or reversal;
- event, effective, posting, creation, and approval timestamps;
- native amount and CAD/USD/BDT currency;
- immutable CAD and BDT conversion-rate snapshots and converted amounts;
- globally unique idempotency key;
- external and evidence references;
- optional migration batch;
- optional one-to-one links to the legacy InvoicePayment and AccountingEntry;
- an optional one-to-one reversal link to the original event;
- DRAFT, POSTED, and REVERSED control states.

### ReceivableAllocation

An append-only link between a posted event and one invoice with:

- signed amount in the invoice currency;
- allocation, posting, creation, and approval timestamps;
- globally unique idempotency key;
- optional exact reversal link;
- DRAFT, POSTED, and REVERSED control states.

The foreign keys use `PROTECT` for financial history. Posted records reject
normal model edits and deletes; corrections must be represented by linked
reversal records.

## Service Boundary

`crm/services/receivables_ledger.py` is the only approved service for future
ledger writes. It currently provides isolated operations to:

1. create draft events with explicit historical rate snapshots;
2. validate and post events atomically;
3. create draft allocations;
4. validate and post allocations atomically;
5. block duplicate idempotency keys;
6. validate cash/accounting-entry amount, date, customer, currency, and
   direction;
7. require evidence and migration-batch references where applicable;
8. validate exact reversal links;
9. return posted invoice allocation totals with one grouped query.

No route, form, dashboard, report, API, or existing payment service imports or
calls this service in Phase 3B.

## Dormant Guarantees

- Migration `0192_receivables_ledger_phase3b` only creates two empty tables,
  indexes, foreign keys, unique keys, and check constraints.
- It contains no `RunPython`, seed, backfill, or production record update.
- `record_invoice_payment` continues to create only the existing
  InvoicePayment and AccountingEntry records and update Phase 3A compatibility
  fields.
- Existing invoice totals, paid amounts, statuses, reports, and dashboard
  formulas remain unchanged.
- Opening receipts, refunds, credit notes, and historical repair remain
  unavailable in active workflows.

## Controls

- Nonzero event and allocation amounts are enforced in the database.
- Stored exchange rates cannot be negative.
- Event and allocation idempotency keys are unique.
- Only authenticated actors can post ledger records.
- Cash receipts require a matching IN AccountingEntry.
- Refunds require a matching OUT AccountingEntry.
- Event, allocation, and invoice currencies must agree.
- Posted allocations cannot exceed their event amount.
- Reversal allocations must exactly offset the linked original allocation.
- Opening receipts require both evidence and migration-batch references.

## Deferred Work

Phase 3B intentionally does not include:

- historical reconciliation or backfill;
- a Finance evidence manifest;
- production data updates;
- dual writing from Phase 3A payments;
- authoritative balance or status reads from the new ledger;
- customer-credit UI or unapplied-credit workflows;
- new routes, permissions, APIs, dashboard values, or reports.

Those changes require separate Phase 3C or later approval after Finance signs a
row-level reconciliation manifest.

## Rollback

Before any deployment, rehearse the migration against a production database
copy and verify both tables remain empty. While the tables are empty, rollback
is the reverse of migration `0192`. Once any receivable event exists, disable
ledger usage and retain the tables instead of dropping financial history.
