# Financial Exception Review Guide

## Scope and safety

The Historical Exception Review Center is restricted to CEO, Finance, and explicitly approved administrators. It records review decisions and evidenced adjustment proposals. It never changes an invoice, payment, supplier obligation, rate, or legacy accounting entry in place.

`FINANCIAL_CORE_WRITES_ENABLED` and `FINANCIAL_CORE_REPORTING_ACTIVE` remain off. Approval of a proposal does not post a journal.

## Loading a copied database

Run only against an identified copied database. Dry-run is the default:

```bash
DJANGO_DB_PATH=/path/to/copy.sqlite3 python3 manage.py load_financial_exceptions
DJANGO_DB_PATH=/path/to/copy.sqlite3 python3 manage.py load_financial_exceptions --apply --actor <approved-user>
DJANGO_DB_PATH=/path/to/copy.sqlite3 python3 manage.py load_financial_exceptions
```

The import uses a SHA-256 source key and stable exception ID. Re-running it is idempotent. Imported source fields are immutable and exception rows cannot be deleted.

## Review process

1. Filter by risk, area, country, currency, party, type, date, status, or evidence state.
2. Open one exception. No approval action is available from the list.
3. Compare current value, expected value, difference, reason, and the exact evidence requirement.
4. Attach documentary evidence. Existing evidence is preserved; a replacement is attached as a new document.
5. Record one confirmed review decision: evidence required, reject proposal, historical data incomplete, or defer.
6. Where evidence proves a value, create an adjustment proposal with amount, original currency, dated rate snapshot, debit/credit accounts, reason, before/after values, and selected evidence.
7. A different authorized approver reviews the proposal and confirms approval or rejection.
8. Approval changes only proposal/review state. Posting is a separate activation-controlled operation.

Unsafe bulk approval is not implemented. Pagination is fixed at 50 records.

## Correction types

| Type | Posting path |
| --- | --- |
| Opening balance | Balanced opening journal after evidence and approval |
| Manual or historical adjustment | Balanced adjustment journal after evidence and approval |
| Reversal | Reversal journal linked to the original posted journal |
| Receivable | Dedicated receivable event service |
| Payable | Dedicated payable event service |
| Currency | Dedicated immutable currency snapshot correction service |

The generic adjustment service refuses to post receivable, payable, or currency corrections through a manual two-line journal.

## Required evidence

| Area | Minimum evidence |
| --- | --- |
| Accounts Receivable | Invoice, customer statement, receipt, matching bank statement |
| Accounts Payable | Supplier bill/statement, payment proof, matching bank statement |
| Currency | Dated bank conversion or approved central-bank rate |
| Cash and bank opening | Named account statement showing date and balance |
| Inventory | Approved count and valuation as of opening date |
| Owner equity | Corporate authorization and matching bank evidence |
| Loans | Executed agreement and lender statement splitting principal/interest |
| Taxes | Filed return, assessment, payment receipt, reconciliation |
| Fixed assets | Purchase document, payment proof, ownership record, asset register |

## Status meaning

- `EVIDENCE_REQUIRED`: insufficient proof; no correction may proceed.
- `APPROVED`: an evidenced correction proposal is approved but not necessarily posted.
- `REJECTED`: the proposal was rejected; source history remains unchanged.
- `INCOMPLETE`: the historical record cannot currently be completed without guessing.
- `DEFERRED`: review intentionally postponed with notes.
- `RESOLVED`: the approved correction has been posted/reversed and reconciled. This status is not used while writes are disabled.

## Rollback

Review decisions are state/audit records. A wrong financial correction is rolled back by reversal, never deletion. A wrong unposted proposal is rejected and replaced. No review action authorizes production activation.
