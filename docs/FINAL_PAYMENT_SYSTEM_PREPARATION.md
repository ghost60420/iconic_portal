# Final payment system preparation

Preparation date: 2026-10-11. **No deployment is authorized or performed.**

## A. October period status

The read-only production audit found two Financial Core periods: August 1–31, 2026 and September 1–30, 2026. Both are OPEN with blank country scope (shared Canada/Bangladesh). No existing periods overlap. No October 2026 Financial Core period, AccountingMonthClose, or AccountingMonthLock exists.

The following proposal is prepared for authorized Finance review; these rows have **not** been created:

| Proposed period | Country | Start | End | Requested state after approval | Existing date/country conflicts |
| --- | --- | --- | --- | --- | --- |
| October 2026 Canada | CA | 2026-10-01 | 2026-10-31 | OPEN | 0 |
| October 2026 Bangladesh | BD | 2026-10-01 | 2026-10-31 | OPEN | 0 |

Record the authorized Finance approver and approval reference before applying either period. Recheck overlapping shared/country-specific periods immediately before configuration. Do not reopen or alter any existing closed period. The general instruction to finish preparation is not treated as Finance authorization to open accounting periods.

## B. Receiving account mapping status

All three configured receiving accounts were audited, including active state, country/currency, account kind, GL type/system mapping, and existing journal use.

| Account | Current configuration | Result / required confirmation |
| --- | --- | --- |
| 1 — SCO | BANK, CA/CAD, active → GL 1020 Canadian Bank Accounts, ASSET, active | Valid for CA/CAD E Transfer and Bank Transfer; six existing GL journal lines |
| 2 — SCO | BANK, CA/CAD, active → GL 6040 Bangladesh Salaries, OPERATING_EXPENSE | Invalid receipt mapping; Finance must identify the actual receiving instrument and approved asset GL; no existing GL journal lines |
| 3 — Taptap | MOBILE, BD/BDT, active → GL 5100 Production Shipping, COGS | Invalid receipt mapping; Finance must confirm instrument type and asset GL; MOBILE does not qualify as one of the four new entry methods; two historical GL journal lines must remain unchanged |

Method availability:

| Method | Canada/CAD | Bangladesh/BDT |
| --- | --- | --- |
| E Transfer | Account 1 available | No eligible account |
| Bank Transfer | Account 1 available | No eligible BANK account |
| PayPal | No configured PAYPAL account | No configured PAYPAL account |
| Cash | No configured CASH account | No configured CASH account |

Existing asset chart entries include Cash in Hand 1010, Canadian Bank Accounts 1020, Bangladesh Bank Accounts 1030, and PayPal and Payment Accounts 1040. Their existence does not establish a correct instrument, country, currency, or allocation. They are candidates for Finance's review only, not approved mappings. `CashBankAccount.gl_account` is one-to-one, so distinct instruments/countries cannot simply reuse a GL already assigned to another account. No accounts, mappings, or historical entries were changed.

## C. Payment workflow results

The existing implementation was retained. Authorized employee entry creates a pending FinanceOperation without changing settled balances. Finance or CEO verifies actual receipt; standard customer receipts then post automatically through the existing Financial Core service, with invoice projection and opportunity synchronization in the same transaction. No CEO approval is needed for normal employee entry or authorized Finance verification. High-risk and production approvals remain separate.

Verification passed for creator/verifier audit information, atomic rollback on posting failure, duplicate form token and repeated verification rejection, invalid account/date/period handling, employee verification denial, scoped permissions, and unchanged non-payment approval controls. Distinct fresh submissions with no transaction reference still require Finance to identify whether the bank receipt is the same; amount alone cannot distinguish a duplicate from two genuine equal payments.

Independent release review found one additional precision defect: a Core BDT multiplier of `0.0134` becomes the ledger's existing six-decimal divisor `74.626866`. Converting that rounded divisor back caused a false exact-equality failure. Validation now compares in the ledger's stored divisor representation. The calculation formulas and amount-equality checks remain unchanged. Regression tests demonstrate both valid posting and continued rejection of a genuinely different rate or a one-cent converted-amount discrepancy. No further code blocker was found by the review.

## D. Invoice 69 and rollback-protected production-data results

The immutable production backup was copied to a dedicated local test database. Synthetic CA Sales/Finance actors existed only inside rollback transactions because no real non-superuser Finance actor currently has CA verification scope. No real user's permissions were changed. September 30 was used solely as a hypothetical date covered by an existing open period; real receipts must not be backdated to bypass October's missing configuration.

| Check | Result |
| --- | --- |
| Actual invoice 69: CAD 500, required deposit 100%, opportunity 210 | Confirmed |
| Employee records CAD 200 | Pending; paid amount remains zero; no journal |
| Finance verifies CAD 200 | Paid 200, balance 300, PARTIALLY PAID, production still blocked |
| CEO verifies remaining CAD 300 | Paid 500, balance zero, FULLY PAID, READY FOR PRODUCTION APPROVAL |
| Separate hypothetical 30% deposit, CAD 150 | DEPOSIT CONFIRMED; balance 350; ready for approval; test configuration rolled back |
| Production | No production order created or started |
| Duplicate token / repeated verification | Rejected; no extra payment or journal |
| Missing October / closed September / accounts 2 and 3 | Correctly rejected |
| Employee attempts verification | Rejected |
| Financial journals | Exactly one balanced journal per verified payment |
| Canada, Bangladesh, combined trial balance and balance sheet | Balanced during simulation; report output exactly restored after rollback |

All **210 database tables** had identical row fingerprints after rollback. The entire test database remained byte-identical to the backup, SHA-256 `0f01448850b2cec5f6d37fda50e0d8b1a6e7e9ae4fa076c94045a85fcdcec13c`. Invoice 69 returned to paid zero and the journal count returned to 8. The final source version was used for a second successful replay.

Artifacts in the private local recovery directory: `rollback-validation.py`, `rollback-validation-final.log`, and `rollback-validation-fingerprints.json`. These contain the reproducible assertions and all-table evidence and are intentionally outside Git.

## E. Performance results

Only read loading changed: eager-loaded existing lifecycle/creator relations, reused the loaded invoice, and combined relationship counts/existence checks with independent indexed subqueries. No financial calculation, displayed workflow selection, schema, index, or stored record was changed. The query does not multiply costings, orders, and shipments through joins.

Invoice 69, same local production snapshot and measurement method:

| Measurement | Before this preparation | After |
| --- | --- | --- |
| Warm query count | 16 | 8 |
| Cold query count | 18 | 10 |
| Warm response time | 17.65 ms | 17.53 ms |
| Cold response time | 795.25 ms | 586.77 ms |

These measure RequestFactory view/template rendering, excluding authentication middleware/network time. Cold is the first render in a fresh process. The two remaining cold-only queries initialize existing shared navigation notification/employee caches; they remain a disclosed cold-start exception to the 8-query target. No broad navigation/cache refactor was made.

The separate payment screen previously measured 6 warm / 8 cold queries and 8.47 ms warm / 611.95 ms cold; it is unchanged in this preparation. Financial dashboard bounded-query tests remain part of the final regression suite.

N+1 verification passed: adding 20 payment rows adds zero queries. Workflow counts with multiple costings/orders/shipments still use one independent-subquery statement. Existing workflow navigation, summary, timeline, and shipment precedence match exactly. The warm invoice detail budget is now met; cold-start and live network timings are not claimed to meet that budget or to be measured after deployment.

## F. Final tests

- Focused invoice performance/costing/customer workflow: **37 passed**, 23.634 seconds.
- BDT precision regression: before fix, three reproducible failures; after fix, **3 passed**, 0.486 seconds.
- Django system check: **passed, no issues**.
- Migration drift check: **passed, no changes detected**.
- Compilation of all **25 changed Python files**: **passed**.
- Final production-snapshot rollback replay: **passed**, all 210 tables and financial reports unchanged.
- Full CRM suite: **777 passed**, 327.820 seconds, **zero failures/errors**, exit status 0. This includes the financial reports, Canada/Bangladesh transactions, invoice payments, Finance/CEO verification, production gates, duplicate prevention, and new precision/performance regressions.

The test fixture import now uses module access to keep discovery unambiguous; no test case was removed. A previous full-suite run lost its temporary log when the session resumed, so no result is claimed for it. The final suite was rerun to completion with durable log and exit-status files in the recovery directory: `final-crm-tests.log` and `final-crm-tests.exit`.

Full-suite command:

```sh
DJANGO_SECRET_KEY=isolated-payment-tests-only DJANGO_DEBUG=1 /usr/local/bin/python3 manage.py test crm.tests --noinput --verbosity 0
```

PyCharm's configured Python executable was verified before each Python invocation. Tests use disposable local databases. No live test payments, sends, historical financial updates, or migrations were performed.

## G. Backup and rollback verification

Production metadata reverified before backup:

- Host/user: `ec2-user@femline.ca`
- Project: `/home/ec2-user/iconic_portal`
- Branch: `codex/finance-live-visibility`
- Live commit: `034826bc09c11288b349c448b74764b6281219dc`
- Live tree: `18cd0f7f1c7d1ae47e43a85f1926080732ec0a8a`
- Remote: `git@github.com:ghost60420/iconic_portal.git`
- Service: `gunicorn.service`, active, working directory matches project
- Tracked production files clean. Existing untracked reconciliation worktree/reports/backups/logs preserved.

Production backup directory:

`/home/ec2-user/iconic_portal/backups/verified-payments-preparation-20261011T215813Z`

Private local recovery directory:

`/Users/hossain/iconic_payment_release_backups/20261011T215813Z`

| Artifact | SHA-256 |
| --- | --- |
| `production.sqlite3` | `0f01448850b2cec5f6d37fda50e0d8b1a6e7e9ae4fa076c94045a85fcdcec13c` |
| `production-code.bundle` | `3717338eb06b6480ab5a92591e045c682c8d2388688275ac7edad653a069157f` |

Backup was made using SQLite online `.backup` with a read-only source connection, not a raw copy of an active database. Server and downloaded checksums match. Server backup and locally restored database passed `PRAGMA integrity_check` (`ok`); `PRAGMA foreign_key_check` returned no violations. The restored database is byte-identical to the immutable backup.

The Git bundle contains complete history through the live commit. `git bundle verify` passed on the server and in the restored repository. A separate clone was checked out at the live commit; commit/tree hashes match and the restored code working tree is clean. The original preparation implementation was also checkpointed locally as `c12196cef2475781c41d16bad57194a50f9ef131` on `codex/verified-invoice-payments` before further edits.

The final tested release is preserved by local tag `verified-payments-prepared-20261011`; its verified production rollback baseline is local tag `verified-payments-production-20261011`, pointing to `034826bc09c11288b349c448b74764b6281219dc`. Both tags remain unpushed. A complete local `prepared-release.bundle` in the recovery directory preserves the prepared branch and rollback tag. The feature checkout is clean after the final commit.

Recovery rehearsal did not replace or restart production. A future rollback must restore/revert code to the verified release baseline while retaining legitimate financial activity. Do not overwrite production with this database snapshot after new business transactions without a separately approved data recovery decision. Refresh the database backup immediately before any eventual deployment because production can change after preparation.

The live audit was repeated after final rollback testing and matched the original audit byte-for-byte: period/account configuration, invoice 69, journal count/balances, and duplicate source-key results were unchanged. GitHub's `codex/finance-live-visibility` ref also matches live commit `034826b`; the prepared feature branch remains local and unpushed.

### Exact release file manifest

All 36 files below belong to the verified payment release or its required tests/reports. Independent review found no unrelated file changes. Compared with production `034826b`:

```text
crm/forms.py
crm/forms_finance_operations.py
crm/models_financial_core.py
crm/services/finance_operations.py
crm/services/financial_journal.py
crm/services/financial_permissions.py
crm/services/payment_reconciliation.py
crm/services/production_payment.py
crm/services/receivable_accounting.py
crm/services/receivables_ledger.py
crm/services/workflow_visibility.py
crm/templates/crm/finance_operations/confirm.html
crm/templates/crm/finance_operations/detail.html
crm/templates/crm/finance_operations/form.html
crm/templates/crm/financial_core/dashboard.html
crm/templates/crm/invoice/invoice_view.html
crm/templates/crm/invoice/partials/payment_entry.html
crm/templates/crm/invoice/partials/payment_verification.html
crm/templates/crm/invoice/payment_entry.html
crm/templates/crm/opportunity_detail.html
crm/tests/test_ceo_gate_invoice_currency.py
crm/tests/test_customer_workflow_improvements.py
crm/tests/test_finance_operations.py
crm/tests/test_financial_core.py
crm/tests/test_invoice_detail_performance.py
crm/tests/test_invoice_payment_live_fix.py
crm/tests/test_local_sewing.py
crm/tests/test_production_order_from_quotation.py
crm/tests/test_receivable_bdt_precision.py
crm/tests/test_verified_invoice_payments.py
crm/urls.py
crm/views_finance_operations.py
crm/views_financial_core.py
crm/views_invoice.py
docs/FINAL_PAYMENT_SYSTEM_PREPARATION.md
docs/VERIFIED_INVOICE_PAYMENTS_REPORT.md
```

Protected email synchronization, automation, WhatsApp, deployment scripts, systemd and nginx files are untouched. There are no model migrations, dependencies, new URLs, or new accounting services. The focused invoice template justification is documented in the implementation report; performance extends existing workflow loading rather than creating parallel business logic.

## H. Remaining blockers

- Authorized Finance approval for October periods.
- Approved corrections for accounts 2/3 and actual receiving-account configuration for missing methods/countries.
- No active non-superuser Finance user currently qualifies to verify CA customer payments. Active BD verifiers exist (user IDs 12, 13, 15, 80). Opportunity 210's current owner (user 17) has BD-only scope and cannot enter this CA invoice's payment. Authorized role/country configuration is needed; this release does not silently widen real user permissions.
- Cold invoice-detail rendering remains at 10 queries because of two shared navigation cache lookups; warm invoice loading meets the 8-query target. This cold-start exception remains explicit rather than being presented as full budget compliance.
- Final deployment authorization, expressly withheld by the user until readiness is reported.

## I. Deployment readiness

**NOT READY FOR DEPLOYMENT while the Finance configuration blockers remain.** Code preparation, final testing, backup creation, and recovery verification are complete. No period/account mutation, push, deployment, or service restart has occurred. No accounting formulas or historical financial rows have been changed. Final authorization is still required even after the configuration blockers are resolved.
