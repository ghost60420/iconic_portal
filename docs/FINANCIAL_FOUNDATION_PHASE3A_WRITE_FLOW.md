# Financial Foundation Phase 3A Write Flow

## Status

- Scope: Phase 3A only
- Purpose: stop new invoice and payment drift
- Historical repair: not performed
- Schema or migration changes: none
- Dashboard, report, API, route, permission, or calculation changes: none
- Phase 3B work: not started

## Write Invariants

Every new invoice begins with these protected values:

| Field | Required value |
| --- | --- |
| `Invoice.status` | `draft` |
| `Invoice.invoice_status` | `DRAFT` |
| `Invoice.paid_amount` | `0.00` |
| `Invoice.approved_at` | `NULL` |
| `Invoice.approved_by` | `NULL` |

An invoice edit cannot directly post `status` or `paid_amount`. The existing
fields remain visible in `InvoiceForm`, but they are disabled and the write
service restores the protected values captured before form binding.

All new payments must:

1. use the same currency as the invoice;
2. use the invoice's Canada or Bangladesh accounting side;
3. have a positive amount;
4. apply only to an active, unarchived invoice;
5. not exceed the current outstanding balance;
6. be posted in an open accounting period;
7. create one `InvoicePayment` and one linked incoming `AccountingEntry` in the
   same transaction; and
8. update the compatibility `paid_amount` and `status` projection while the
   invoice row is locked.

Phase 3A does not add unapplied credit, refund, credit-note, reversal, or
receivables-ledger storage. Those writes are explicitly rejected instead of
being represented as fake payments or destructive edits.

## Invoice Write Service

`crm/services/invoice_state.py` is the only production writer for protected
invoice financial state.

| Operation | Service function | Result |
| --- | --- | --- |
| Create invoice | `create_draft_invoice` | Forces draft, pending approval, and zero paid amount |
| Edit invoice details | `save_invoice_details` | Restores protected state and preserves the existing status projection formula |
| Approve/send invoice | `approve_invoice` | Atomically writes `sent`, `APPROVED`, approver, approval time, and audit |
| Cancel/void invoice | `void_invoice` | Writes `cancelled`, archives the invoice, preserves payments, and records the reason |
| Apply/remove payment projection | `write_payment_projection` | Updates only the compatibility paid amount and payment-derived status |

The service also exposes read-only canonical interpretations:

- document: `DRAFT`, `ISSUED`, or `VOIDED`;
- approval: `PENDING` or `APPROVED`; and
- settlement: `UNPAID`, `PARTIAL`, or `SETTLED`.

No canonical read is persisted in a new field in Phase 3A.

## Payment Write Service

`crm/services/payment_reconciliation.py` owns payment transaction writes.

### Record Payment

`record_invoice_payment`:

1. starts an atomic transaction;
2. locks the invoice with `select_for_update`;
3. validates invoice, currency, side, period, amount, and balance;
4. saves the `InvoicePayment` using the existing exchange-rate conversion;
5. creates the existing incoming `AccountingEntry`;
6. links the two records;
7. updates the invoice compatibility projection through
   `invoice_state.write_payment_projection`; and
8. writes the existing accounting and invoice audit records.

### Delete Existing Payment

`delete_invoice_payment` preserves the current approved workflow. It locks the
invoice and payment, enforces accounting-period and shared-entry protections,
records deletion audits, removes the linked records, and recalculates the
existing compatibility projection. Phase 3B must replace destructive deletion
with permanent reversal events after separate approval.

### Refunds and Credit Notes

`record_refund` and `apply_credit_note` deliberately raise
`UnsupportedReceivableOperation`. There is no approved schema capable of
recording these operations without corrupting cash receipts or invoice totals.

## Protected Production Locations

| Location | Phase 3A protection |
| --- | --- |
| `crm/forms.py:InvoiceForm` | Direct `status` and `paid_amount` posts disabled |
| `crm/views_invoice.py:invoice_add` | Uses `create_draft_invoice` |
| `crm/views_invoice.py:invoice_add_ca` | Uses `create_draft_invoice` |
| `crm/views_invoice.py:invoice_add_bd` | Uses `create_draft_invoice` |
| `crm/views_invoice.py:invoice_edit` | Uses protected snapshot and `save_invoice_details` |
| `crm/views_invoice.py:invoice_approve` | Uses `approve_invoice` |
| `crm/views_invoice.py:invoice_delete_or_void` | Void path uses `void_invoice` |
| `crm/views_invoice.py:invoice_payment_add` | Uses `record_invoice_payment` |
| `crm/views_invoice.py:invoice_payment_delete` | Uses `delete_invoice_payment` |
| `crm/services/costing_workflow.py:create_invoice_from_costing` | Uses `create_draft_invoice` |
| `crm/services/costing_workflow.py:create_invoice_from_quick_costing` | Uses `create_draft_invoice` |

Invoice preview objects remain unsaved and are not financial writes. Test
fixtures may still construct historical states directly because they are not
production write paths.

## Historical Data Isolation

Phase 3A contains no query that bulk-updates invoices, payments, or accounting
entries. Existing invoice and payment rows remain unchanged until a user
performs a new approved action through an existing route. Existing differences
remain visible to the read-only reconciliation helper; they are not repaired.

## Test Coverage

Focused tests cover:

- protected invoice creation;
- approval metadata and compatibility status;
- partial and full payments;
- outstanding balance and reconciliation difference;
- overpayment, currency, and side rejection;
- cancellation with payment preservation;
- refund and credit-note blocking;
- read-only form guards; and
- static protection against direct writes in active production writers.

Existing invoice, payment deletion, costing conversion, dashboard, reporting,
currency, permission, and finance regression suites must pass before deployment.

## Rollback

Revert the Phase 3A service integration commit. No migration rollback or data
conversion is required. Payments recorded while Phase 3A is active remain valid
ordinary `InvoicePayment` and `AccountingEntry` records under the existing
schema and must not be deleted as part of a code rollback.

## Remaining Before Phase 3B

Phase 3B requires separate CEO approval for additive receivable events,
allocations, unapplied customer credits, credit notes, refunds, reversals,
opening balances, historical adjustments, and any historical repair manifest.
