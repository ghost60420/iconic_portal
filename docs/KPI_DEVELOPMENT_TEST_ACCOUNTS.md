# KPI Development Test Accounts

## Access

Open `http://127.0.0.1:8010/accounts/login/`.

Passwords are not stored in Git or this document. The repository owner can
retrieve the owner-only credential handoff from:

`/Users/hossain/CRM Production Backups/kpi-development-integration-20260729T210032Z/KPI_UAT_CREDENTIALS.txt`

Do not paste those credentials into tickets, screenshots, chat, or committed
files.

## Accounts

| Test role | Username | Intended scope |
| --- | --- | --- |
| CEO / Super Admin | `kpi_dev_ceo` | All authorized KPI pages, setup, reports, approvals, and locks |
| Manager | `kpi_dev_manager` | Assigned employees and review queue |
| Employee, one role | `kpi_dev_employee` | Own Performance, Dashboard, reports, and notifications |
| Employee, several roles | `kpi_dev_multi` | Own 60/25/15 role breakdown |
| Director | `kpi_dev_director` | Authorized department review, approval, and lock |
| HR | `kpi_dev_hr` | Authorized performance scope without private bonus amounts |
| Accounts | `kpi_dev_accounts` | Own performance; no KPI setup or review authority |

These accounts exist only in the copied development database. They are not
production identities.

## Safety

- External email, SMS, WhatsApp, payroll, payment, and Marketing providers are
  disabled. The local Marketing dashboard remains available for regression.
- No live scheduler is running.
- KPI notifications remain inside the development CRM.
- The local development URL is not a public staging service.
- Delete or rotate the credential handoff after UAT is complete.
