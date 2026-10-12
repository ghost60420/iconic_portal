# Final deployment preparation — 12 October 2026

**NOT READY FOR DEPLOYMENT. No push, deployment, service restart, live payment, production order, period opening, or account remapping was performed.**

Preparation extends `codex/final-crm-integration` from `5e735c1994d06cdade5af710766c63fbdb8345a1`. This report supersedes the remaining-test and performance status in `FINAL_CRM_INTEGRATION_RELEASE.md`; that document retains the earlier integration history and full workflow design. Current financial configuration is based on the production snapshot captured at 22:08:59 UTC.

## A. October financial periods

October 1–31, 2026 is prepared for Canada (`CA`) and Bangladesh (`BD`). Existing August and September periods are OPEN and global. Read-only checks found zero overlapping period pairs and zero October conflicts in either country.

Neither October period was created or opened. Explicit named CEO authorization is still required by the task. Recheck overlaps before any authorized configuration write. See [the concrete approval package](FINAL_FINANCIAL_CONFIGURATION_APPROVAL.md).

## B. Receiving accounts

| Receiving record | Existing mapping | Result |
| --- | --- | --- |
| 1: SCO, CA/CAD BANK | Asset GL 1020, Canadian Bank Accounts | Structurally valid for Bank Transfer and E Transfer; actual receiving instrument still requires confirmation. |
| 2: SCO, CA/CAD BANK | Expense GL 6040, Bangladesh Salaries | Invalid for customer receipts. Confirm duplicate versus distinct bank instrument and its approved asset mapping. |
| 3: Taptap, BD/BDT MOBILE | COGS GL 5100, Production Shipping | Invalid for the requested receipt methods. Two posted transfers reference it; preserve those records pending an authorized accounting review. |

Existing active asset GLs 1030 (Bangladesh Bank), 1040 (PayPal/payment accounts), and 1010 (Cash in Hand) have no receiving records. They are candidates for confirmed configuration, not evidence of actual bank/PayPal/cash instruments. The exact required country, currency, instrument and custodian confirmations are in the approval package. Receiving-account GL mapping is one-to-one; GL 1020 is already used by record 1. No expense/COGS account was converted into a bank or asset, and no opening balance was invented.

## C. Payment and Financial Core workflow

Verified on the existing invoice-entry and Finance-verification screens and through regression tests:

1. An employee records date, amount, method and receiving account from the invoice, with optional reference, receipt and notes.
2. Entry is AWAITING VERIFICATION. It does not count as received or reduce the settled invoice balance.
3. Authorized Finance or CEO verification calls the existing Financial Core service. Posting, verified payment, invoice balance and opportunity synchronization succeed or fail in one transaction.
4. The same verified records appear in invoice, Finance and production readiness views. Duplicate entry/verification and duplicate journal source keys are blocked.

Canada and Bangladesh, partial/full receipts, deposits, invalid accounts, closed/missing periods, role restrictions, posting failures and report accuracy are covered by the passing suite. Entry requires no extra CEO approval; payment verification remains separate. Existing refund, reversal and other high-risk controls remain. No new payment system or accounting formula was introduced.

## D. Production workflow and invoice 69 / opportunity 210

Existing costing and quotation approvals are recognized. Sampling requires full verified payment; bulk uses the actual approved invoice deposit terms. Payment verification updates readiness but never starts production. An employee with existing opportunity and production access explicitly clicks Move to Production as operational release. CEO/Super Admin exceptions retain a reason, approver, timestamp, opportunity and waived requirements in audit history, without changing balances or inventing payments.

Actual snapshot values remain:

- Opportunity 210 / OPP-L1406-001, The Karma Projects: Awaiting Payment, no production order.
- QC-68: approved sample costing, seven pieces. QQT20260037: existing CEO quotation recognized without another approval request.
- Invoice 69 / INV00057: CAD 500 total, CAD 0 verified received, **CAD 500 outstanding**. Its historical opportunity link is null; the existing quick-costing relationship resolves opportunity 210 without rewriting the invoice.
- The commercial blocker is full verified sample payment. October posting separately requires the authorized period and valid account configuration.

On disposable copies only, CAD 200 entered by Sales and verified by Finance followed by CAD 300 entered by Sales and verified by CEO produced two legitimate receipts and balanced journals. Readiness became READY FOR PRODUCTION. No production order appeared until the explicit release click. Desktop CEO and mobile Manager release tests each created exactly one sample order in separate rollback sessions. No repeated quotation approval was requested. The real invoice was never marked paid.

## E. Performance and final code changes

The scoped opportunity already loaded by the view is now reused for navigation/favorites/recent views within the same request and user. A redundant chatter ownership query was removed while keeping module authorization and the initial row scope. Existing recent-view records use a single UPDATE, retaining timestamps, labels, uniqueness and the 20-record limit. Write authorization remains independent.

A browser check found an approved Quick Costing quotation still showing “No quote yet” in the existing commercial widget. That widget now reads the same production-readiness quotation and approval result, adding no queries.

| Opportunity 210 full request | Cold queries | Warm queries | Cold response | Warm response range |
| --- | ---: | ---: | ---: | ---: |
| Starting 5e735c1 | 34 | 30 | 1173.41 ms | 32.86–37.74 ms |
| Final preparation | 28 | 24 | 589.64 ms | 39.74–71.32 ms |
| Final plus five costing/invoice pairs | 28 | 24 | 557.13 ms | 36.18–72.68 ms |

These are measured local Django full requests under concurrent test load, not production browser/network timings. Query count improved by 20%; warm latency improvement is not established. All requests returned HTTP 200. Added related records did not grow query count: no observed N+1. The eight-query detail-page target remains unmet. Remaining queries serve distinct data widgets, authentication, permission/audit state and recent-view tracking; reaching eight requires further work beyond safe duplicate-query removal. No cross-request financial cache or broad protected-module refactor was introduced.

## F. Desktop/mobile and regression results

Desktop Chrome and Safari at a 390 × 844 responsive viewport exercised invoice payment entry, verification, Finance visibility, opportunity readiness and explicit production release on local rollback copies. Required fields and financial/readiness cards were usable at mobile width. The existing navigation scrolls horizontally. Finance and CEO verification, an unauthorized role denial, missing-period validation and duplicate verification rejection were observed. No application HTTP 500 occurred in these checks.

Browser QA is responsive emulation, not a physical-device or live deployment test. Historical product image files were not copied into the private test media directory, producing expected image 404s; their visual rendering is unverified. Application screens and static styles loaded. No live browser release verification is claimed before deployment.

| Verification | Final result |
| --- | --- |
| Full `crm.tests`, including Finance and Production | **823 passed**, 357.086 seconds, exit 0 |
| `aihub leadbrain marketing whatsapp` suites | **167 passed**, 66.081 seconds, exit 0 |
| Focused platform/readiness regression | **21 passed**, 14.650 seconds; included in the full CRM suite |
| `manage.py check` | No issues |
| `makemigrations --check --dry-run` | No changes |
| Changed release Python files | 42 compiled successfully |
| `git diff --check` | Passed |
| Independent scope/permission review | Passed; no access expansion found |
| Fresh production-copy workflow rehearsal | Passed; all 210 table fingerprints unchanged after rollback |
| Trial balance, balance sheet, profit/loss | Unchanged after rollback for ALL, CA and BD |
| Journal reconciliation | All 8 original journals / 16 lines balanced in native, CAD and BDT; no duplicate nonempty source keys |
| Final mobile browser session | Two posted test receipts, two journals and one explicitly released order; journal checks passed before complete rollback |

There are **990 passing regression tests** in the final two full suites, with no failing accounting test bypassed. A preliminary focused command included one nonexistent test-module label; the corrected 21-test run and both final full suites passed.

## G. Backup and rollback

Fresh server backup: `/home/ec2-user/iconic_portal/backups/final-preparation-20261012T220859Z/`.

Private local evidence: `/Users/hossain/iconic_final_integration_backups/final-preparation-20261012/`.

- Database SHA-256: `72dbf46938921295dc6ccf198cf62acd3ebbbf116907aa4f83a596488ff878d4`.
- Production code bundle SHA-256: `40d3f205bcef56a7f4452b80683d5bfcc20deb2207e9b4189f11eb7773453a2f`.
- SQLite online backup integrity check: `ok`; foreign-key check: no violations. Source, restored copy, fresh workflow rollback copy and final browser rollback copy all retained the same database hash.
- Production bundle verification, restored clean checkout and `git fsck --full` passed at live HEAD `034826bc09c11288b349c448b74764b6281219dc`, tree `18cd0f7f1c7d1ae47e43a85f1926080732ec0a8a`.
- The final preparation commit and local tag are recorded in private `FINAL-PREPARATION-CHECKPOINT.txt`, together with candidate bundle verification and restore results. No remote push is needed to preserve this local checkpoint.

Rollback procedure for a later authorized deployment: first record the current live HEAD/status and take a new backup; preserve newer legitimate transactions. The verified production bundle restores the original code into a separate clean checkout. With no migrations in this release, prefer a reviewed code-only rollback to that checkpoint. Do not reset an unrelated dirty worktree or overwrite the live database with this snapshot. Any database restore requires a separate recovery plan accounting for receipts posted after backup. Recheck access, service configuration and target HEAD before executing any deployment/rollback commands.

## H. Exact files changed in this preparation

1. `crm/services/platform_tools.py` — scoped request reuse and recent-view query reduction.
2. `crm/views.py` — reuse the authorized opportunity and avoid redundant chatter lookup.
3. `crm/templates/crm/opportunity_detail.html` — consistent approved quotation display.
4. `crm/tests/test_platform_control_center.py` — request/user/scope cache and recent-view regressions.
5. `crm/tests/test_opportunity_production_readiness_ui.py` — approved Quick Costing quotation display regression.
6. `docs/FINAL_FINANCIAL_CONFIGURATION_APPROVAL.md` — exact period/account approval package.
7. `docs/FINAL_DEPLOYMENT_PREPARATION.md` — this report.

The combined release has 58 changed files relative to live `034826b`: the 54 listed in the earlier integration report plus `crm/services/platform_tools.py`, `crm/tests/test_platform_control_center.py` and these two new documents. The original dirty `/Users/hossain/iconic_portal` worktree was not staged or changed by this preparation. Protected email sync/outbox/automation, marketing, WhatsApp, deployment scripts, systemd and nginx are unchanged. No model, migration, accounting formula or historical financial record changed in this preparation.

## I. Remaining blockers and deployment status

**STOP — NOT READY FOR DEPLOYMENT.**

1. Explicit CEO authorization to create/open October 2026 for CA and BD is missing.
2. Confirmed Canadian receiving instrument, account 2 identity/asset mapping, Bangladesh bank, PayPal and cash configuration are missing. Account 3's posted transfer history must be preserved; its correction needs a separately approved plan. Exact questions and existing asset candidates are in the approval package.
3. The eight-query detail-page performance budget remains unmet at 24 warm queries; no claim of meeting it is made.

After authorized configuration, rerun its country/date/account validations, financial regressions and balance/duplicate checks; then obtain separate final CEO deployment authorization and a fresh backup. Live code remained on `codex/finance-live-visibility` at `034826b` when inspected. Recheck live changes before merging this release so unrelated work is preserved.

Post-deployment verification is pending authorization and an actual deployment: check invoice 69 payment form, Finance verification/Core, opportunity 210/readiness, authorized Move to Production, lead detail, desktop/mobile rendering, server errors, automation health, balanced journals, unique source keys and historical-record invariance. Monitor these after release; do not claim they have passed on live candidate code now.
