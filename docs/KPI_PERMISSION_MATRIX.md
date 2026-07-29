# KPI Performance Permission Matrix

| Capability | Employee | Manager | Director | HR | CEO | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View own approved/locked history | Yes | Yes | Yes | Yes | Yes | Yes |
| View another employee | No | Assigned only | Department only | All | All | All |
| Open review queue | No | Yes | Yes | Yes | Yes | Yes |
| Create review | No | Assigned only | Department only | No | All non-self | All non-self |
| Edit Draft | No | Assigned only | Department only | No | All non-self | All non-self |
| Submit Draft | No | Assigned only | Department only | No | All non-self | All non-self |
| Start approval review | No | No | Department only | No | All non-self | All non-self |
| Approve or reject | No | No | Department only | No | All non-self | All non-self |
| Lock approved review | No | No | Department only | No | All non-self | All non-self |
| Edit approved history | No | No | No | No | No | No |
| Edit templates/formulas/assignments | No Stage 5 route | No Stage 5 route | No Stage 5 route | No Stage 5 route | No Stage 5 route | No Stage 5 route |

## Enforcement

- Login is required for every Stage 5 route.
- State-changing routes require POST and Django CSRF validation.
- Querysets and direct-object views are scoped independently.
- Workflow services recheck actor scope and self-approval restrictions.
- Existing CRM roles are read through the current operations-permission service.
- Stage 5 adds no permission fields, groups, middleware rules, or public API.
- Manager assignment grants access only to that assigned employee review.

## Stage 7 Dashboard Matrix

| Capability | Employee | Manager | Director | HR | CEO | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Open KPI dashboard | Yes | Yes | Yes | Yes | Yes | Yes |
| View own approved KPI | Yes | Yes | Yes | Yes | Yes | Yes |
| View another employee | No | Assigned only | Department only | All | All | All |
| View review queue | No | Assigned only | Department only | All | All | All |
| View department ranking | No | Team only | Department only | All | All | All |
| View Canada/Bangladesh summary | No | No | No | No | Yes | Yes |
| View employee leaderboard | No | Assigned only | No | No | All | All |
| View eligibility summary | Own state only | Assigned only | No | No | All | All |
| View bonus forecast amounts | No | No | No | No | Yes | Yes |
| Apply filters | Own data only | Assigned only | Department only | All | All | All |
| Generate exports | No | No | No | No | No | No |
| Edit from dashboard | No | No | No | No | No | No |

Stage 7 applies scope in the service querysets and verifies the requested widget
against a server-side role registry. Each route requires authentication.
Employee, manager, and director filters can only narrow their existing scope;
they cannot expand it. Stage 7 adds no role, permission, middleware, or public
API.

## Stage 8 Intelligence and Report Matrix

| Capability | Employee | Manager | Director | HR | CEO | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Open Intelligence Center | Own view | Assigned team | Department | HR scope | All | All |
| View Red/Yellow/Green intelligence | Own only | Assigned team | Department | HR scope | All | All |
| View employee analytics | Own only | Assigned team | Department | HR scope | All | All |
| View manager analytics | No | Own team/workload | Department | HR scope | All | All |
| View department analytics | No | No | Department | HR scope | All | All |
| View Canada/Bangladesh comparison | No | No | No | No | Yes | Yes |
| View bonus readiness state | Own state without amount | Assigned state without amount | No amount | No amount | All authorized | All authorized |
| View bonus amount | No | No | No | No | Yes | Yes |
| Use intelligence action links | Own allowed targets | Assigned targets | Department targets | HR targets | All authorized | All authorized |
| Export employee performance | Own only | Assigned team | Department | HR scope | All | All |
| Export department/manager reports | No | Assigned team only | Department | HR scope | All | All |
| Export executive/bonus reports | No | No | No | No | Yes | Yes |
| Publish intelligence rules | No | No | No | No | Yes | Yes |

Every Stage 8 page, widget, report, and action route requires authentication.
The service applies employee, manager, and department scope before filters.
Signed action links are rechecked against current server authorization. HR
scope does not imply financial bonus-amount access.

## Stage 9 Notification Matrix

| Capability | Employee | Manager | Director | HR | CEO | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View KPI notifications | Own only | Assigned alerts | Department alerts | HR alerts | Company alerts | Company alerts |
| View retained notification history | Own only | Own only | Own only | Own only | Own only | Own only |
| Mark read or dismiss | Own only | Own only | Own only | Own only | Own only | Own only |
| See bonus amount in an alert | No | No | No | No | No | No |
| Follow an action link | Own scope | Assigned team | Department | HR scope | All authorized | All authorized |
| Publish notification rules | No | No | No | No | Yes | Yes |

Every Stage 9 route requires authentication. Notification visibility is
recipient-specific in both the view and service. Destination pages recheck
their existing server authorization; an action URL does not grant access.
Stage 9 changes no groups, permission fields, middleware, or external delivery
permissions.
