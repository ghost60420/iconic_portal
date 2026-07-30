# KPI Private Staging Access Guide

## URL

`https://kpi-staging.100-51-11-48.sslip.io/`

This is not the production domain.

## Access Method

Access requires two authentication layers:

1. Enter the HTTP Basic Auth credentials supplied by the Iconic CRM
   administrator.
2. At `/accounts/login/`, enter one of the staging CRM test accounts.

Passwords are not stored in Git or documentation. The owner-only handoff is
stored on the server at:

`/home/ec2-user/iconic_kpi_staging/config/KPI_STAGING_ACCESS_CREDENTIALS.json`

It is also retained in the owner-only deployment evidence directory. Request
credentials through the existing private internal channel from the Iconic CRM
server administrator. Do not place credentials in tickets, screenshots, email,
or chat.

## Test Accounts

| Role | Username |
| --- | --- |
| CEO / Super Admin | `kpi_dev_ceo` |
| Manager | `kpi_dev_manager` |
| Employee, one role | `kpi_dev_employee` |
| Employee, several roles | `kpi_dev_multi` |
| Director | `kpi_dev_director` |
| HR | `kpi_dev_hr` |
| Accounts | `kpi_dev_accounts` |

These are staging-only identities. Passwords should be rotated or the accounts
disabled after UAT.

## Login

1. Open the staging URL.
2. Complete the browser Basic Auth prompt.
3. Enter the assigned staging username and password.
4. Confirm the displayed name begins with `Staging`.
5. Confirm the URL remains on the staging hostname before entering test data.

## Access Issues

Contact the Iconic CRM server administrator through the existing internal
support channel. Include the time, staging URL, browser, and whether the failure
occurred at Basic Auth or CRM login. Do not include a password.

## Bug Reporting

Use the bug template in `docs/KPI_STAGING_UAT_GUIDE.md`. Attach screenshots from
the staging URL only and remove any credential prompt or password-manager data
before submitting.
