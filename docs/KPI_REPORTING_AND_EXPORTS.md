# KPI Reporting and Exports

## Reports

Stage 8 supports:

1. Executive KPI summary
2. Department performance
3. Manager performance
4. Employee performance
5. Review completion
6. Critical Red
7. Bonus readiness
8. Monthly comparison
9. Quarterly comparison
10. Annual comparison

Each report is built from the same authorized intelligence services used by
the page. Report objects contain the title, date range, active filters,
generation time, generating user, organization scope, summary score and
status, tabular results, risks, recommended actions, and a snapshot-source
note.

## Formats

- PDF: ReportLab document with printable metadata, summary, risks, actions, and
  the complete authorized result table.
- Excel: OpenPyXL workbook with plain rows, frozen header, and an autofilter.
- CSV: UTF-8 tabular data plus report metadata.
- Print: authenticated HTML optimized with print CSS.

Spreadsheet cells beginning with `=`, `+`, `-`, or `@` are neutralized.
Snapshot comments and report text are stripped of HTML. Exports contain no raw
hidden model fields.

## Authorization

Authorization is applied in both report building and the view:

- Employee: own allowed reports and own approved records only.
- Manager: assigned-team reports only.
- Director: authorized-department reports only.
- HR: workforce performance scope without bonus amounts.
- CEO and Super Admin: company reports and authorized immutable bonus amounts.

Filters can narrow but never expand service scope. Guessed report types,
action tokens, employees, or URLs are rejected server-side. Report routes
require authentication and GET.

On-screen alert, employee, and bonus widgets remain paginated. Their report
builders explicitly request the complete authorized service result, so exports
are not truncated to the first widget page.

## Currency

Amounts come only from immutable Stage 6 records. Values retain their stored
currency, use the existing CRM currency formatter, and are never combined or
converted. CAD values use the existing Canadian display convention.

## Audit

The existing CRM audit table records report generation, export, sensitive
bonus-report access, and filtered employee-report generation. Logs store event
metadata and target URL, not private report content.
