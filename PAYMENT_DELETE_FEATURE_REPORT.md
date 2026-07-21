# Payment Delete Feature Report

Generated: 2026-07-21

## Summary

Added a safe POST-only invoice payment deletion workflow for Invoice Payment History.

The change allows CEO/Admin/Accounts/Finance role users to remove a mistaken payment while recalculating invoice paid amount, balance, and status. Other users remain view-only. No database records were changed during development or copied-production testing.

## Files Changed

- `crm/views_invoice.py`
- `crm/urls.py`
- `crm/templates/crm/invoice/invoice_view.html`
- `crm/tests/test_invoice_payment_delete.py`
- `PAYMENT_DELETE_FEATURE_REPORT.md`

## Behavior Added

- Delete button appears beside each payment in Invoice Payment History for authorized users only.
- Delete uses `POST` with CSRF protection.
- A deletion reason is required.
- Browser confirmation shows:
  - payment amount
  - payment date
  - payment method
  - warning that invoice balance and financial reports will update
- Deletion is idempotent:
  - if the payment was already removed, totals are not changed again.
- Locked accounting periods block direct deletion with:
  - `This payment is in a locked accounting period. Create a reversal entry instead.`
- Invoice status is recalculated after deletion:
  - paid amount `0` -> `sent` / unpaid
  - paid amount between `0` and invoice total -> `partial`
  - paid amount equal to or above invoice total -> `paid`
- Legacy paid amount is preserved by subtracting only the deleted payment amount from `Invoice.paid_amount`.

## Permissions

Can delete:

- Superuser
- CEO role
- Admin role
- Accounts role
- Finance role

Cannot delete:

- Sales and other non-accounting users
- View-only users

Note: deletion permission intentionally does not rely on legacy `UserAccess.can_accounting_bd` because that field defaults to true for newly created users.

## Accounting Safety

- If a payment has a linked `AccountingEntry`, the entry is deleted only when no other `InvoicePayment` shares it.
- If the accounting month is closed or locked, deletion is blocked.
- Unrelated accounting entries are untouched.
- `AccountingEntryAudit` receives a `DELETE` snapshot before the payment-owned entry is removed.
- `CRMAuditLog` stores a permanent payment deletion audit record containing:
  - deleted payment ID
  - invoice ID and number
  - original amount
  - currency
  - payment date
  - payment method
  - deleted by
  - deleted time
  - deletion reason
  - accounting entry ID

## Copied Production Database Test

Copied DB path:

- `db_rehearsal.sqlite3`

Source:

- read-only copy from `/home/ec2-user/iconic_portal/db.sqlite3`

Execution:

- deletion tests were run inside `transaction.atomic()`
- `transaction.set_rollback(True)` was used
- copied DB changes were rolled back
- live production DB was not touched

Rollback rehearsal results:

- duplicate payment deletion: `302`, paid recalculated to `700.00`, status `paid`, duplicate removed, kept payment preserved
- partial payment deletion: `302`, paid recalculated from `500.00` to `300.00`, status `partial`, balance `700.00`
- full payment deletion: `302`, paid recalculated to `0.00`, status `sent`, payment key `unpaid`
- unauthorized Sales user: `403`, payment preserved
- duplicate delete request: second request returned safe message, paid stayed `0.00`, payment count stayed `0`
- CAD/USD/BDT currency separation: `BDT:3000.00; CAD:0.00; USD:200.00`
- locked period deletion: blocked, payment preserved
- audit record created: yes

## Invoice 52 Duplicate Payment Audit

Invoice:

- ID: `52`
- Number: `INV00040`
- Total: `CAD 1,850.00`
- Current paid amount: `CAD 3,700.00`
- Current balance: `CAD -1,850.00`
- Payment count: `2`

Duplicate candidates:

- Payment ID `21`
  - Date: `2026-07-09`
  - Amount: `CAD 1,850.00`
  - Method: `bank`
  - Accounting Entry: `168`
- Payment ID `22`
  - Date: `2026-07-09`
  - Amount: `CAD 1,850.00`
  - Method: `bank`
  - Accounting Entry: `169`

Expected result if deleting either payment ID `21` or `22`:

- Paid amount after deletion: `CAD 1,850.00`
- Balance after deletion: `CAD 0.00`
- Invoice status after deletion: `paid`

No payment was deleted from invoice 52.

## Dashboard And Report Impact

The feature updates the source financial records used by:

- invoice paid amount
- invoice outstanding balance
- invoice payment status
- Accounts Receivable
- Financial Dashboard
- CEO Dashboard
- Daily CEO Briefing
- customer financial summary
- monthly payment and cash reports
- currency totals
- payment history

No dashboard formulas were changed. Existing dashboards continue to calculate from invoices, payments, and accounting entries.

## Performance Review

- Invoice detail GET adds no new database queries. The delete form uses the existing `payment_history` list and existing selected fields.
- No N+1 query was introduced in payment history rendering.
- Delete POST performs bounded lookups for one invoice, one payment, optional linked accounting entry, month-close checks, audit writes, and invoice recalculation.

## Test Results

Passed:

- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py check`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py makemigrations --check --dry-run`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_invoice_payment_delete`
  - 9 tests passed
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_ceo_gate_invoice_currency crm.tests.test_finance_dashboard_enhancement`
  - 31 tests passed
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_final_financial_stabilization crm.tests.test_ceo_daily_briefing_metrics`
  - 24 tests passed
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests`
  - 545 tests passed
- `DJANGO_SECRET_KEY=local-test-secret python3 -m py_compile crm/views_invoice.py crm/urls.py crm/tests/test_invoice_payment_delete.py`
- `git diff --check`
- `git diff --cached --check`

Migration dry-run:

- `No changes detected`

## Risk Assessment

Risk level: Medium-low.

Main risks:

- Payment deletion is a destructive financial action, so permission and audit controls must be reviewed carefully before deployment.
- Accounting entry deletion assumes the linked entry was created only by that payment. The view blocks deletion when another payment shares the same entry.
- Closed accounting periods block deletion and require a manual reversal entry.

Mitigations:

- POST-only endpoint with CSRF.
- Required reason.
- Confirmation window.
- Explicit role check.
- Idempotent second request handling.
- Permanent CRM audit record.
- Accounting audit snapshot.
- Full regression passed.

## Deployment Recommendation

Do not deploy yet until reviewed.

If approved, deploy only:

- `crm/views_invoice.py`
- `crm/urls.py`
- `crm/templates/crm/invoice/invoice_view.html`
- `crm/tests/test_invoice_payment_delete.py`
- `PAYMENT_DELETE_FEATURE_REPORT.md`

No migration is required.
