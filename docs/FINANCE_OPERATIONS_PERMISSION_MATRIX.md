# Finance Operations Permission Matrix

## Role access

| Capability | CEO / Super Admin | Finance | Accounts | Director / Manager | Sales | Production | HR | Normal staff |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Open Operations Center | Full | Assigned sides | Assigned sides | Assigned approval scope | Owned customer status | Assigned-side production | Assigned-side payroll | No |
| Submit customer, supplier, expense, bank, owner, loan, asset, inventory | Yes | Yes | Yes | No | No | No | No | No |
| Submit production/factory cost | Yes | Yes | Yes | No | No | Yes, assigned side | No | No |
| Submit payroll | Yes | Yes | Yes | No | No | No | Yes, assigned side | No |
| Review standard assigned department items | Yes | Yes | No | Supplier bill, expense, utility, production/factory cost | No | No | No | No |
| Review high-risk items | Yes | Yes | No | No | No | No | No | No |
| Post to Financial Core | Yes | Yes | No | No | No | No | No | No |
| View full debit/credit preview | Yes | Yes | Yes | No | No | No | No | No |
| View bank account identity | Yes | Yes | Yes | No | No | No | No | No |
| View employee payroll detail | Yes | Yes | No | Approved department totals only | No | No | Yes | No |
| Add expense category | Yes | Yes | Yes | No | No | No | No | No |

## Scope rules

- Canada/Bangladesh access comes from `UserAccess` accounting flags and team side.
- Finance and Accounts see only assigned sides unless CEO/Super Admin.
- Sales can see customer operation status only when the linked invoice is inside existing salesperson/lead ownership scope.
- Production can see and submit only Production Cost and Factory Daily Cost for its assigned side.
- HR can see and submit only payroll for its assigned side.
- Director/Manager approval requires an accounting flag and a matching `EmployeeProfile.department_ref`.
- Managers can see approved department payroll totals, but not employee detail or payroll evidence outside their authorization.
- Suppliers, bills, production orders, Quick Costings, payment accounts, and employees are scoped in form querysets and revalidated on submit.

## Enforcement points

1. Route decorator rejects roles without Operations Center access.
2. Workflow lookup returns 404 when the role cannot submit that type.
3. Form querysets remove out-of-scope objects.
4. `submit_operation`, `review_operation`, and `post_operation` repeat permission checks inside the service boundary.
5. Detail/review/preview/post object lookup uses `scope_finance_operations_for_user`.
6. Evidence downloads call `can_access_financial_object` on the parent operation.
7. Sales, department, production, payroll, bank, supplier, and country scopes are server-side; URL changes cannot broaden them.

## Independent approval

All high-risk operations block approval by their submitter. High risk includes refunds, credits, supplier payments, payroll, withdrawals/transfers, owner and loan activity, asset purchases, inventory adjustments, and customer overpayment warnings. Every review action requires decision notes. Approval requires evidence, except Factory Daily Cost when an approved daily-rate source is retained.

## Sensitive fields

- Payment account names are replaced with `Restricted` outside bank-detail roles.
- Full account debit/credit lines are omitted outside sensitive-financial roles.
- Employee identity is stored only in protected payroll lines; journal/report output remains departmental.
- Owner/equity operations are not visible to Sales, Production, HR, Manager, or normal staff.
- Historical correction tools remain governed by the separate exception/adjustment permission controls.

## Infrastructure condition

Application authorization is enforced and tested. Production activation still requires an infrastructure check proving that protected finance media is not served directly by Nginx or another web server. This repository does not contain that configuration.
