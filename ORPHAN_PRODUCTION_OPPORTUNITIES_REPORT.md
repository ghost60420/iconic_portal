# Orphan Production Opportunities Report

Date: 2026-07-17

## Scan Rule

Find opportunities where:

- `Opportunity.stage = "Production"`
- No `ProductionOrder` exists with `ProductionOrder.opportunity_id = Opportunity.id`

The `Opportunity` model does not have a direct `production_order_id` field, so this report uses the production order relationship as the source of truth.

## Production Scan Result

Production branch:

- `codex/historical-data-entry-mode`

Production commit:

- `edd1cec822b6e52f1594b09c693e161234b1e71f`

Orphan count:

- `6`

## Orphan Records

| Opportunity PK | Opportunity ID | Customer ID | Lead ID | Quick Costing | Quick Status | Invoice | Invoice Status | Paid | Total | Invoice Order ID | Lifecycle ID | Lifecycle Status | Lifecycle Production Order ID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 83 | OPP-LHT7ZA96GE-001 | 601 | 1232 | - | - | - | - | - | - | - | - | - | - |
| 84 | OPP-LYW565MOPX-001 | 601 | 1233 | - | - | - | - | - | - | - | - | - | - |
| 88 | OPP-L1FVY1ZC58-001 | 593 | 1243 | - | - | - | - | - | - | - | - | - | - |
| 89 | OPP-LFMAQPYX1A-001 | 593 | 1245 | - | - | - | - | - | - | - | - | - | - |
| 182 | OPP-IN-1007-001 | 593 | 1555 | 43 | invoiced | INV00030 | partial | 8000.00 | 22990.00 | - | 57 | invoice | - |
| 185 | OPP-CWBJ8JGGL9-001 | 597 | - | 45 | quoted | - | - | - | - | - | - | - | - |

## Deployment Handling

This deployment is approved to repair Opportunity 182 only.

The other five records are reported only. They should be reviewed separately before any data repair because they do not have the same fully traced Quick Costing, quotation, invoice, and lifecycle chain as Opportunity 182.

## UI Protection

After deployment, any opportunity matching this orphan condition will show:

- `Broken Production State` badge
- Warning banner explaining that the opportunity is marked Production but has no linked ProductionOrder

## Data Safety

This report does not modify production data.

