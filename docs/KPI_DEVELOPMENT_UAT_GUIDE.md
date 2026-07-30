# KPI Development UAT Guide

## Start

1. Open `http://127.0.0.1:8010/accounts/login/`.
2. Choose the username for the role in
   `docs/KPI_DEVELOPMENT_TEST_ACCOUNTS.md`.
3. Obtain its password through the owner-only credential handoff. Do not record
   the password in feedback.
4. Record every result in the CEO Feedback table in
   `docs/KPI_DEVELOPMENT_BROWSER_TEST_REPORT.md`.

The URL is local to this development machine. Do not switch settings, databases,
or provider flags during UAT.

## CEO / Super Admin

| Page and URL | Action | Expected result |
| --- | --- | --- |
| KPI Dashboard, `/performance/dashboard/` | Open **KPI & Performance > Executive Dashboard** and change a filter | Company widgets reload independently from approved snapshot data |
| KPI Policies, `/kpi/setup/policies/` | Inspect draft status, bonus, intelligence, and notification successors | Development policies and permitted controlled transitions appear |
| KPI Assignments, `/kpi/setup/assignments/` | Select a development employee and preview roles | Up to three roles appear and activation requires exactly 100 percent |
| Review Queue, `/performance/reviews/` | Open the submitted development review | The authorized review appears without out-of-scope employee data |
| Review Detail, `/performance/reviews/<review-id>/` | Approve and lock a submitted development review | State becomes Locked and inputs become read only |
| Intelligence, `/kpi/intelligence/` | Open health, alert, and trend sections | Executive widgets load from stored snapshots |
| Bonus, `/kpi/intelligence/#bonus-readiness` | Inspect readiness | Authorized immutable readiness records appear; no payment action exists |
| Reports, `/kpi/intelligence/#reports` | Generate PDF, Excel, CSV, and Print | Each format downloads or opens with authorized data |
| Notifications, `/notifications/` | Open a KPI notification and mark it read | The internal CRM event opens; no external message is sent |

## Manager

| Page and URL | Action | Expected result |
| --- | --- | --- |
| Review Queue, `/performance/reviews/` | Open the assigned employee | Only assigned employees appear |
| Review Detail, `/performance/reviews/<review-id>/` | Enter allowed KPI values and save Draft | The draft persists without changing template weights |
| Review Detail | Submit the saved review | The Stage 4 engine produces the score and state becomes Submitted |
| Out-of-scope Performance URL | Open the Accounts employee Performance route | HTTP 403 |

## Director

| Page and URL | Action | Expected result |
| --- | --- | --- |
| Review Queue, `/performance/reviews/` | Open a submitted review in the authorized department | The review is visible and approval controls appear |
| Review Detail | Reject with a reason | Review returns to Draft and transition history is retained |
| Review Detail | After correction and resubmission, approve and lock | Review becomes read only and an immutable snapshot appears |

## Employee With One Role

| Page and URL | Action | Expected result |
| --- | --- | --- |
| My Performance, use the navigation link | Open current and historical sections | 100 percent role, manager, score, status, current review, history, comments, and improvement items appear read only |
| KPI Dashboard, `/performance/dashboard/` | Open each visible widget | Only the employee's information appears |
| Another employee Performance URL | Open directly | HTTP 403 |
| My Performance at 390x844 | Review the full page | No horizontal overflow and controls remain usable |

## Employee With Several Roles

| Page and URL | Action | Expected result |
| --- | --- | --- |
| My Performance, use the navigation link | Inspect assigned roles | North America Sales 60, Project Manager 25, and Administration 15 percent appear |
| My Performance | Add the displayed weights | Total is exactly 100 percent |
| Review history | Open a locked period | Stored role definitions and scores remain unchanged |

## HR

| Page and URL | Action | Expected result |
| --- | --- | --- |
| Review Queue, `/performance/reviews/` | Open an authorized Performance record | Performance and workflow data are visible |
| Bonus Readiness, `/kpi/intelligence/#bonus-readiness` | Inspect the record | Private bonus amounts are withheld without bonus authorization |

## Accounts

| Page and URL | Action | Expected result |
| --- | --- | --- |
| My Performance, use the navigation link | Open current assignment and history | The Accounts user's own performance appears |
| KPI Assignments, `/kpi/setup/assignments/` | Open directly | HTTP 403 |
| KPI Policies, `/kpi/setup/policies/` | Open directly | HTTP 403 |

## Feedback

For every check, record:

- date and role
- page or workflow
- `PASS` or `FAIL`
- screenshot path when useful
- concise observed result
- defect identifier for a failure

Use the CEO Feedback table in
`docs/KPI_DEVELOPMENT_BROWSER_TEST_REPORT.md`. Do not include passwords, private
employee data, or exported bonus amounts.

Do not enable email, SMS, WhatsApp, payroll, payment providers, external
marketing actions, schedulers, production policies, or production deployment
during UAT.
