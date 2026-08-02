# Financial Permission Matrix

## Enforcement

Financial Core views use `require_financial_permission`. Superusers pass all checks. Role checks reuse `operations_role_names`; transaction management and approval also require an existing Canada or Bangladesh accounting access flag. Querysets, object lookups, downloads, and report side parameters are re-scoped on the server. An unauthorized object identifier returns 404 and a requested country outside the user's scope is ignored.

| Capability | CEO / Super Admin | Finance | Accounts | Director / Manager | Sales | Production | HR | Normal staff |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| View Financial Core operational reports | Yes | Assigned sides | Assigned sides | Assigned sides and approval scope | Owned invoice status/download only | Production costs only | Payroll only | No |
| View P&L, Balance Sheet, Cash Flow, bank details | Yes | Yes | Yes | No | No | No | No | No |
| Create bills, receipts, expenses, accounts | Yes | Yes with accounting flag | Yes with accounting flag | No | No | No | No | No |
| Approve financial transactions | Yes | Yes with accounting flag | No | Yes with accounting flag | No | No | No | No |
| Enter production costs | Yes | Assigned sides | Assigned sides | View/approve in assigned sides | No | Assigned side | No | No |
| View employee-level payroll | Yes | Yes | No | No | No | No | Yes | No |
| Approve payroll | Yes | Yes with accounting flag | No | No employee-level access | No | No | No | No |
| View owner equity and company-wide profit | Yes | Yes | Accounts for sensitive reports | No | No | No | No | No |
| View other employee commissions | Yes | Finance payroll detail | No | No | No | No | HR payroll detail | No |

## Data minimization

- Payroll journal lines contain department totals, not employee identity.
- Employee details remain in protected `PayrollLine` records and are rendered only by the payroll-detail permission.
- Cash/bank account references are masked in the model/UI; account configuration is sensitive.
- Sales users have a scope predicate but no broad Financial Core report route.
- Production users can enter production costs but cannot view company-wide profit.
- Manager/Director expense access is restricted to their `EmployeeProfile.department_ref` and accounting side.
- Supplier bills, payroll batches, bank accounts, exceptions, and adjustments use server-side object scopes.
- Historical adjustment approval enforces evidence, reason, before/after values, and independent approval.
- Invoice PDF, Financial Core evidence, legacy accounting attachments, and accounting documents are served by authorization-checking views.
- Financial exports require the sensitive-financial export permission and are side scoped.

## Deployment prerequisite

Application authorization is complete and tested. Before production activation, infrastructure must verify that Nginx does not serve `/media/financial_core/`, `/media/accounting/`, or `/media/accounting_docs/` directly. Those prefixes must reach the authorized download views or be denied. This repository contains no Nginx configuration, and project policy forbids guessing or editing the production service configuration.
