# Finance Operations Center Guide

## Scope and safety state

The Finance Operations Center is the daily-entry layer above the existing Financial Core. It gives employees business forms while retaining the existing Chart of Accounts, journal, receivable, payable, expense, payroll, production-cost, currency, and reporting services.

The layer is intentionally dormant for accounting posting:

- `FINANCIAL_CORE_WRITES_ENABLED=False`
- `FINANCIAL_CORE_REPORTING_ACTIVE=False`

Employees can submit requests, attach evidence, obtain independent approval, and inspect the posting preview while both flags are off. Approval never posts. A posting attempt while writes are off is rejected, audited as `POST_BLOCKED`, and leaves the request `APPROVED`.

## Main screen

Route: `/accounting/operations/`

The center shows only actions the current role may submit. It also shows scoped counts for pending approval, evidence required, approved but unposted, today's activity, and blocked posting attempts. Recent activity is scoped by country, role, customer ownership, department, and workflow type.

The requested daily actions are represented by 18 primary cards plus Pending Financial Approvals and Today's Financial Activity. Related customer credit, bank/cash, fee, loan-interest, and shareholder workflows are available from the contextual links on their form pages.

## Request lifecycle

1. **Submit:** the form validates party, business side, currency, amount, account, source record, evidence, and workflow-specific rules.
2. **Snapshot:** the dated CAD and BDT conversion values are resolved once and stored on `FinanceOperation`.
3. **Preview:** the service resolves account system keys and stores a read-only posting preview. Views never contain hard-coded account IDs.
4. **Review:** an assigned approver opens a separate confirmation screen and approves, rejects, or requests more evidence with decision notes.
5. **Post:** Finance or CEO opens a second confirmation containing the final preview. Posting is permitted only when the operation is independently approved and the Core-write flag is enabled.
6. **Audit:** submission, evidence, review, blocked posting, posting, and controlled inventory balance changes create `FinancialAuditEvent` records.

Submitted operations cannot be deleted. Approved operations lock all financial, relationship, currency, and source-snapshot fields. Corrections must use the existing reversal, refund, credit-note, or adjustment controls.

## Evidence and duplicate controls

Every daily form requires a supporting document except Factory Daily Cost, where the approved rate-default snapshot is the evidence source. Additional evidence can be uploaded while a request is pending or evidence-required. Downloads pass through the authorized Financial Core document endpoint; changing a document ID or operation URL outside the user's scope returns 404.

Customer payment references are checked per customer. A duplicate can be submitted only after an explicit warning acknowledgement and reason, and remains subject to approval. Supplier bill number duplication for the same supplier is blocked. No duplicate is silently merged or overwritten.

## Customer operations

Customer payments show the current invoice balance and require invoice currency and side. Partial payment remains partial, exact settlement closes the invoice, and an excess is allowed only when the user explicitly requests unapplied customer credit. A second balance check occurs inside the locked posting transaction so a changed invoice balance cannot create an unapproved credit.

Refunds, credit notes, and unapplied credits are separate high-risk requests. They never edit the original invoice or payment. Posting delegates to the existing receivable accounting and protected payment-reconciliation services.

## Supplier, expense, and utility operations

Supplier bills remain unpaid when created and may later receive partial or full payments. Supplier, bill, payment account, currency, and business side must agree.

Company Expense accepts configured suppliers or a vendor name for paid expenses. Unpaid expenses require a configured supplier and due date so the existing payable ledger can own the balance. Authorized Finance/Accounts/CEO users can add an `ExpenseCategory` from the same screen without changing code; categories map to Chart-of-Accounts system keys.

Utility entry supports electricity, hydro, water, gas, internet, telephone, generator fuel, and other utility. The form displays the previous 12 accessible entries and applicable monthly budgets. A paid utility posts through the expense service; an unpaid utility creates a supplier obligation through that same service.

## Payroll privacy

Payroll accepts an employee-level record or a department total, never both. Gross pay must equal deductions plus net pay. Employer cost is recorded separately. Employee selections are restricted to the user's business side.

CEO, Finance, and authorized HR may see employee detail. Managers may see only approved department totals. Sales, Production, and normal staff cannot retrieve payroll operations or documents. Journal lines contain department totals, not employee identity.

## Production and factory cost

Production costs are tied to an accessible Production Order and inherit its customer and opportunity. Supplier, currency, business side, estimated amount, actual amount, variance, payment state, and evidence are retained. Posting creates an approved supplier bill and production-cost source; an explicitly paid cost also creates its supplier payment.

Factory Daily Cost links an active Quick Costing to an approved daily factory-rate default. The operation stores the submitted daily amount. Posting uses that immutable amount even if the default changes later. Estimated and actual timeline cost and profit use the existing factory timeline formulas and status rules. This costing-only workflow does not create a General Ledger journal.

## Bank, owner, loan, asset, and inventory operations

Bank/cash transfers validate source and destination account kinds, sides, currencies, and historical conversion snapshots. Cross-currency transfers require an evidenced destination amount whose CAD equivalent reconciles. Company transfers use clearing entries and never revenue or expense.

Owner, loan, and shareholder flows are high risk and require Finance/CEO approval. Owner investment and loan proceeds are financing inflows. Owner withdrawal, principal repayment, and shareholder repayment reduce equity or liabilities. Only loan interest is an expense.

Asset purchases capitalize to the selected asset class. They do not post the full purchase to operating expense.

Inventory adjustments select an existing active `InventoryItem`, record quantity, evidenced unit cost, direction, reason, and evidence. Posting locks the item, prevents negative stock, creates the balanced inventory journal, updates quantity, creates `InventoryMovement`, and audits before/after values. Purchases also create a supplier bill. No adjustment runs while Core writes are off.

## Operational pages

- `/accounting/operations/approvals/`: 50-row pagination and filters for side, currency, type, department, risk, and status.
- `/accounting/operations/activity/`: 50-row transaction and decision feeds with date, side, department, currency, type, and user filters.
- `/accounting/operations/<id>/posting-preview/`: simple explanation for scoped users; full debit/credit detail only for sensitive Finance/CEO roles.
- `/accounting/operations/<id>/post/`: separate final confirmation. It remains a safe blocked action while writes are disabled.

## Activation boundary

This layer is ready for copied-database posting rehearsal. It is not production activation authorization. Production posting still requires completed historical reconciliation, approved opening balances, resolved activation checklist, verified protected-media routing, backup/restore evidence, and written CEO approval.
