# Financial Permission Completion Report

## Completed application controls

- Country scope is derived from `UserAccess.can_accounting_ca`, `can_accounting_bd`, and role; query parameters cannot widen it.
- Finance/Accounts queries are restricted to assigned business sides.
- Sales invoice access uses assigned Opportunity, Quick Costing salesperson, production order Opportunity, and Lead ownership.
- Manager/Director expenses are restricted to their employee department and permitted side.
- Supplier bills/payments, production costs, payroll batches, cash/bank accounts, bank reconciliations, journals, exceptions, and adjustments use object-scoped querysets.
- Employee-level payroll is CEO/Finance/authorized-HR only. HR cannot pay payroll or access bank/profit reports.
- Bank detail and financial export permissions are CEO/Finance/Accounts only.
- Exception/correction tools are CEO, Finance, or explicitly approved Admin only.
- Adjustment creation requires reason, before/after values, rate snapshots, balanced accounts, and evidence.
- Approval is restricted to approved roles; creator self-approval is rejected.
- Approval does not post. Posting remains flag-gated and uses a balanced journal/reversal or dedicated subledger service.
- Completed Financial Core records and evidence cannot be deleted. Legacy accounting delete now directs users to adjustment/reversal.

## Download and export controls

- Invoice PDF lookup is salesperson/side scoped.
- Financial Core document lookup resolves the source object and rechecks access.
- Legacy accounting attachment and accounting document downloads filter by the user's assigned side.
- Accounting CSV/XLSX export requires sensitive export permission and applies server-side side scope.
- Bank references stored/rendered by Financial Core are masked references.
- Unauthorized object identifiers return 404 to avoid confirming record existence.

## Verification

The readiness suite covers side isolation, salesperson ownership, department expense scope, payroll privacy, bank privacy, cross-side document URL tampering, evidence upload, confirmation workflow, maker-checker approval, immutable exception sources, disabled posting, pagination, and query bounds.

- Readiness and focused financial suite: 46 passed, 0 failed.
- Full CRM regression: 830 passed, 0 failed in 350.251 seconds.
- `python3 manage.py check`: no issues.
- `python3 manage.py makemigrations --check --dry-run`: no changes detected.
- Changed Python modules compile successfully.

## Performance

| Page | Baseline queries | Final queries | Cold ms | Warm ms |
| --- | ---: | ---: | ---: | ---: |
| Financial dashboard | 10 | 10 | 17.04 | 9.87 |
| Exception Review Center | Not implemented | 5 | 80.33 | 10.76 |
| Permission controls | Not implemented | 2 | 2.95 | 2.49 |
| Trial Balance | Not separately recorded | 3 | 5.39 | 3.48 |
| Balance Sheet | Not separately recorded | 6 | 4.46 | 4.42 |
| Profit and Loss | Not separately recorded | 8 | 5.06 | 5.15 |
| Cash Flow | Not separately recorded | 4 | 5.90 | 5.77 |
| AR Aging | Not separately recorded | 4 | 7.03 | 6.58 |
| AP Aging | Not separately recorded | 4 | 3.80 | 3.38 |

Exception pagination was verified with 55 allowed rows and a 50-row page; query count stayed within 10. Report queries are grouped, and dashboard remained at its established 10-query budget. No repeated exchange-rate query loop was observed.

## Remaining infrastructure gate

The repository does not contain production Nginx configuration. Before activation, operations must prove that the web server does not expose these storage prefixes directly:

- `/media/financial_core/`
- `/media/accounting/`
- `/media/accounting_docs/`

Templates now use authorized application endpoints, but infrastructure denial must be verified in the deployment environment. This is an activation blocker, not a reason to alter Nginx without the required production review.
