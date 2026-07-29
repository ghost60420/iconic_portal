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
