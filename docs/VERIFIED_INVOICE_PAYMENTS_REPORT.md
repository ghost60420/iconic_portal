# Verified invoice payments — investigation and implementation report

Date: 2026-10-11. Release status: **NOT DEPLOYED**.

This is the implementation-phase snapshot. See [Final payment system preparation](FINAL_PAYMENT_SYSTEM_PREPARATION.md) for the subsequent configuration audit, backup/rollback verification, performance improvements, current tests, and release blockers.

Implementation is isolated in `/Users/hossain/iconic_verified_invoice_payments`, branch `codex/verified-invoice-payments`, based on the inspected live commit `034826bc09c11288b349c448b74764b6281219dc`. The original `/Users/hossain/iconic_portal` checkout has extensive unrelated changes; none were edited, staged, reset, or committed by this task.

## A. Root cause

1. **Invoice 69 cannot accept today's payment because no October financial period exists.** Live periods are August 1–31 and September 1–30, 2026, both open and shared-country. Neither covers October 11. The existing Financial Core validation correctly rejects posting. No period was opened or created.
2. **Receiving-account configuration is incomplete.** Account 1, SCO, CA/CAD, is active and mapped to active asset account 1020 / CANADIAN_BANK. It is valid for this invoice. Account 2 is mapped to operating expense 6040 / BANGLADESH_SALARIES, and account 3, BD/BDT Taptap, is mapped to COGS 5100 / COGS_PRODUCTION_SHIPPING. Those are not valid receipt asset mappings. No valid BD/BDT, PayPal, or cash receiving account was found. Their replacement mappings cannot safely be inferred.
3. **An entirely empty account selector for invoice 69 was not reproducible with the inspected CEO context:** account 1 qualifies and appears as `SCO (CAD)`. A different user's country access or a PayPal/cash choice can prevent an eligible selection. The form now gives explicit missing-configuration instructions; it does not expose invalid accounts.
4. **The invoice route was restricted twice**, by a superuser check and an accounting URL wrapper. Authorized Sales employees could not enter their customer's payment.
5. **Entry and verification were inconsistent:** the invoice route directly posted, while Finance Operations used submit → approval → separate posting. The view automatically posted CEO-approved operations but not ordinary Finance approvals.
6. **Production eligibility ignored the invoice deposit requirement:** the shared helper used a zero-percent requirement and permitted an unpaid invoice. It now enforces the existing invoice deposit percentage.
7. **The Bangladesh bridge mixed exchange-rate conventions:** legacy invoice/payment accounting uses BDT per CAD as a divisor; Financial Core uses CAD per BDT as a multiplier. The integration now converts between these existing conventions and verifies equal converted amounts. Accounting formulas and historical rows were not changed.

## B. Existing and resulting workflow

Before: restricted invoice entry could immediately write payment/accounting rows; the alternative Finance Operations path had separate approval/post actions and CEO-specific automatic posting.

Now: authorized Sales/Accounts/Finance/CEO entry → existing `FinanceOperation` pending verification → authorized Finance or CEO verifies actual receipt → existing posting service posts a standard payment automatically → existing invoice and opportunity synchronization runs in the same transaction.

Entry alone creates no `InvoicePayment`, journal, settled invoice balance, or production eligibility. Sales retains invoice ownership and country scope. Entry and verification remain separate permissions. CEO authority, self-approval confirmation, high-risk review, refunds, transfers, reversals, and existing adjustment controls remain in place. High-risk customer credit/duplicate-warning cases are not automatically posted as standard receipts.

## C. Changes implemented

- Reused Finance Operations as the pending-payment record and the existing Financial Core receipt posting service. No models, migrations, accounting subsystem, background jobs, or new URLs were added.
- The existing `/invoices/<id>/payments/add/` route renders a focused template rather than the full invoice detail. This template is necessary to keep the payment screen simple and within its query budget; it reuses partials and the existing base layout.
- Required visible fields: date, amount received, method, receiving account. Optional: transaction reference, notes, receipt. Methods: E Transfer, PayPal, Bank Transfer, Cash.
- Currency, country, production association, and configured exchange rates are server-owned. Configuration/date/account errors render form errors instead of posting or returning a validation-related HTTP 500.
- Shared status presentation appears on invoice detail, opportunity readiness, Finance Operations center/activity/approval views, and the Financial Core dashboard. Existing executive finance totals continue reading the same settled records.
- Duplicate form submissions use a unique operation identifier; customer transaction references are checked across invoices. Customer/invoice locks and the existing journal source key prevent repeated posting. A blank-reference payment re-entered through a new form cannot be identified as the same bank transaction from amount alone; Finance must verify the actual receipt.

Status definitions:

| Status | Meaning |
| --- | --- |
| NOT PAID | No settled payment and no active entry |
| PAYMENT RECORDED | Draft payment exists; no settled balance change |
| AWAITING VERIFICATION | Submitted payment awaits receipt verification |
| PARTIALLY PAID | Verified payment exists below the required deposit |
| DEPOSIT CONFIRMED | Verified payment satisfies the deposit but not the full invoice |
| FULLY PAID | Verified balance covers the invoice total |

These are payment/readiness labels, not replacements for document lifecycle statuses. Where a partial/full payment already exists, its settled status remains visible and additional pending entries are listed separately.

## D. Financial Core integration result

Standard verification posts through `post_operation` → `record_invoice_payment` → existing `record_customer_receipt`/journal service. Verification, posting, legacy projection, invoice balance, and opportunity synchronization are atomic. A posting failure rolls the verification back to pending. Period and receiving-account validity are checked again at posting.

Existing creator, verifier, verification timestamp, amount, receiving account, invoice, opportunity, audit events, source record, and journal references are preserved through FinanceOperation and the existing related records. The InvoicePayment creator remains the entering employee. Pending money is never counted as received.

Canada and Bangladesh fixtures passed, including equality of BDT legacy and Core CAD amounts. Existing financial reporting, historical receivable reconciliation, reversal, stabilization, and executive dashboard tests passed. The read-only live baseline had 8 balanced journals and no duplicate journal source keys; this is a pre-deployment observation, not a post-deployment claim.

## E. Opportunity integration result

The existing invoice-to-opportunity resolver handles direct links and costing/order links. This matters for invoice 69: its direct opportunity field is empty, but quick costing 68 links to opportunity 210. Verification uses that link, the existing opportunity synchronization, and shared payment/readiness calculations. No duplicate opportunity payment model was introduced.

## F. Production readiness result

The existing invoice deposit percentage is enforced. Zero/unverified or insufficient payments do not unlock production. A verified qualifying deposit yields `DEPOSIT CONFIRMED` and `READY FOR PRODUCTION APPROVAL`; a fully settled invoice displays `FULLY PAID` with the same readiness. Existing production approval requirements remain. Verification does not create or start a production order.

Tests that previously required unpaid production conversion were updated to the explicitly requested deposit rule. Unrelated CRM behavior passed the broader regression suite.

## G. Invoice 69 verification

Observed live baseline: invoice `INV00057`, CAD 500 total, CAD 0 paid, draft, 100% required deposit, opportunity 210 through quick costing 68, no payment or FinanceOperation.

Testing used a consistent SQLite snapshot made through a read-only source connection and copied locally. No real payment was recorded on the live database.

- October 11 submission: rejected with `No Financial Core period is configured for the journal date and side.`
- Rollback-only hypothetical September 30 submission, using the existing open period and valid account 1: pending operation, paid balance still zero, no journal.
- Verification in the local transaction: exactly one balanced CAD 500 journal, invoice paid 500/balance 0, related opportunity 210, `FULLY PAID`, `READY FOR PRODUCTION APPROVAL`, no ProductionOrder created.
- Repeat verification: rejected, no second journal.
- Rolled back the whole transaction. SHA-256 fingerprints of all rows in Invoice, InvoicePayment, AccountingEntry, JournalEntry, JournalLine, FinanceOperation, ReceivableEvent, ReceivableAllocation, Opportunity, and ProductionOrder were identical before and after.

September 30 was only a test scenario; it is not a recommendation to backdate a real payment.

## H. Verification results and limitations

| Check | Result |
| --- | --- |
| Financial/payment/production targeted suite | 251 passed, 57.815 seconds |
| Full CRM suite | 769 passed, 317.400 seconds, zero failures/errors |
| Later permission changes + customer workflow + Finance Operations | 83 passed, 32.006 seconds |
| `manage.py check` | Passed: no issues |
| `manage.py makemigrations --check --dry-run` | Passed: no changes detected |
| `python -m py_compile` for all 22 changed Python files | Passed |
| `git diff --check` | Passed |
| Invoice 69 rollback simulation | Passed; all 10 model fingerprints unchanged |
| Local browser review | Form rendered, SCO account available; entering 150 changed projected balance to 350; no submission made |

Full suite command, run in the isolated checkout:

```sh
DJANGO_SECRET_KEY=isolated-payment-tests-only DJANGO_DEBUG=1 /usr/local/bin/python3 manage.py test crm.tests --noinput --verbosity 0
```

Later focused run:

```sh
PYTHONPATH=/tmp DJANGO_SECRET_KEY=isolated-payment-tests-only DJANGO_DEBUG=1 /usr/local/bin/python3 manage.py test crm.tests.test_verified_invoice_payments.VerifiedInvoicePaymentTests crm.tests.test_customer_workflow_improvements crm.tests.test_finance_operations --settings=iconic_payment_target_settings --keepdb --noinput --verbosity 0
```

The temporary target settings only select a disposable local test database. Python executable was verified against PyCharm's configured environment before invocation.

An earlier full-suite attempt using a file-backed test database failed in asynchronous calendar/shipment tests with closed-connection errors; the final run above used Django's default in-memory test database and passed. Earlier production tests encoded the old zero-deposit rule and were updated. A check invocation without local debug mode rejected the deliberately non-production test secret; rerunning in the explicit local test configuration passed. There are no unresolved final financial test failures.

### Performance

Measured locally with a production snapshot, fresh user object, RequestFactory view/template rendering, and captured SQL. Cold means first render in a fresh process; warm means the next render. These are not live network or authenticated-middleware timings, and are not load-test estimates.

| Surface | Before cold/warm queries | After cold/warm queries | Before cold/warm ms | After cold/warm ms |
| --- | --- | --- | --- | --- |
| Invoice 69 payment endpoint | 22 / 20 | 8 / 6 | 619.78 / 25.05 | 611.95 / 8.47 |
| Full invoice 69 detail | 22 / 20 | 18 / 16 | 619.78 / 25.05 | 604.50 / 18.61 |

Payment endpoint meets the detail budget in this measured context. **The full invoice detail remains above the 8-query budget**, despite improvement. Existing workflow/lifecycle lookups are the remaining overhead; this task does not claim release readiness or silently expand into a shared workflow refactor. Additional focused optimization is required before release approval under AGENTS.md.

Finance approval queue: 6 queries, cold 18.74 ms, warm 17.77 ms; approval detail: 7 queries, warm 9.24 ms in the full suite. Financial Core dashboard's existing bounded-query test passed at its 10-query ceiling. Payment status list test evaluates six records in one query, with no per-row query growth. No load test at 100 employees / 100,000 leads was performed.

Security regression coverage includes Sales ownership, country scope, separate verification permission, CEO confirmation, denied direct employee posting, multi-role permission preservation, inactive account rejection, closed periods, duplicate submission/verification, and atomic failure rollback. No unrelated permission grants, email sync changes, automation changes, WhatsApp changes, or deploy/service configuration changes were made.

## I. Exact files changed

Application Python:

- `crm/forms.py`
- `crm/forms_finance_operations.py`
- `crm/models_financial_core.py`
- `crm/services/finance_operations.py`
- `crm/services/financial_journal.py`
- `crm/services/financial_permissions.py`
- `crm/services/payment_reconciliation.py`
- `crm/services/production_payment.py`
- `crm/services/receivable_accounting.py`
- `crm/services/receivables_ledger.py`
- `crm/urls.py`
- `crm/views_finance_operations.py`
- `crm/views_financial_core.py`
- `crm/views_invoice.py`

Templates:

- `crm/templates/crm/finance_operations/confirm.html`
- `crm/templates/crm/finance_operations/detail.html`
- `crm/templates/crm/finance_operations/form.html`
- `crm/templates/crm/financial_core/dashboard.html`
- `crm/templates/crm/invoice/invoice_view.html`
- `crm/templates/crm/invoice/payment_entry.html` (new)
- `crm/templates/crm/invoice/partials/payment_entry.html` (new)
- `crm/templates/crm/invoice/partials/payment_verification.html` (new)
- `crm/templates/crm/opportunity_detail.html`

Tests:

- `crm/tests/test_ceo_gate_invoice_currency.py`
- `crm/tests/test_customer_workflow_improvements.py`
- `crm/tests/test_finance_operations.py`
- `crm/tests/test_financial_core.py`
- `crm/tests/test_invoice_payment_live_fix.py`
- `crm/tests/test_local_sewing.py`
- `crm/tests/test_production_order_from_quotation.py`
- `crm/tests/test_verified_invoice_payments.py` (new)

Report: `docs/VERIFIED_INVOICE_PAYMENTS_REPORT.md` (new).

## J. Deployment status, blockers, and rollback

**Not committed, pushed, or deployed. No live database configuration or historical financial record was changed.**

Inspected live Git metadata: host `femline.ca`, SSH user `ec2-user`, folder `/home/ec2-user/iconic_portal`, branch/tracking branch `codex/finance-live-visibility`, commit `034826bc09c11288b349c448b74764b6281219dc`, origin `https://github.com/ghost60420/iconic_portal.git`. Live tracked files were clean; untracked reconciliation worktree/reports/backups/logs existed and were left alone. This feature branch has not been selected or merged as a production deployment source. The restart service must be reverified before any deploy commands are proposed.

Outstanding release work:

1. Finance must provide approved accounting-period dates/country scope and receiving-account-to-GL mappings. Do not invent mappings or reopen closed periods. Invoice 69 can use account 1, but an approved period covering the actual payment date is still required.
2. Resolve the remaining full invoice detail query-budget breach and rerun affected regression/performance checks.
3. Recheck live/local Git state, select the exact deployment source/target, verify the service, show exact commands, and create a fresh database backup before deployment. The investigation snapshot does not replace the required pre-deployment backup.
4. After deployment, verify the authenticated live payment screen, Financial Core balances/source-key uniqueness, unchanged historical invoice/opportunity records, logs, production approvals, and monitoring. These post-deployment checks have not occurred.

Current rollback is code-only: leave this isolated branch undeployed or discard only this task's isolated changes after review. Production needs no rollback because it was not changed. A future release should revert only the focused release commit, preserve financial history, and use existing approved reversal controls for any real transactions; do not restore an old database over subsequent business activity. Stop deployment on any accounting-test failure.
