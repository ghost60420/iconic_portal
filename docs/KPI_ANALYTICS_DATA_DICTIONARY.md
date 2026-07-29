# KPI Analytics Data Dictionary

## Insight

| Field | Meaning |
| --- | --- |
| `insight_type` | Stable intelligence category |
| `status` | Green, Yellow, Red, or source workflow state |
| `severity` | Configured low, medium, high, or critical importance |
| `title` | Short user-facing result |
| `summary` | Sanitized evidence summary |
| `supporting_value` | Current supporting measure |
| `previous_value` | Comparable prior measure |
| `change_percentage` | Decimal percentage change when valid |
| `employee` | Authorized employee display name |
| `manager` | Authorized manager display name |
| `department` | Existing department label |
| `location` | Existing location label |
| `review_period` | Stored review period |
| `source_record` | Stable source identifier, not private content |
| `recommended_action` | Human action suggestion |
| `action_link` | Server-signed, scope-checked route |
| `generated_date` | Intelligence generation timestamp |
| `intelligence_version` | Stage 8 service contract version |

## Trend

| Field | Meaning |
| --- | --- |
| `current_result` | Latest approved snapshot result |
| `previous_result` | Previous comparable approved result |
| `difference` | Current minus previous |
| `percentage_change` | Difference divided by previous when valid |
| `direction` | Improving, declining, stable, or insufficient |
| `status` | Configured intelligence status |
| `periods_included` | Number of comparable periods |
| `data_quality_warning` | `Insufficient History` or another limitation |

## Core Measures

- Overall KPI: stored approved Stage 5 employee score.
- Review completion: completed approved/locked reviews divided by visible
  employees for the selected period.
- Bonus readiness: immutable Stage 6 eligibility state. Amounts are visible
  only to executive users and retain the stored currency.
- Department average: mean of latest approved employee results in that
  authorized department scope.
- Manager average: mean of latest approved results for assignments managed by
  that authorized manager.
- Status counts: stored final Green, Yellow, and Red review statuses.
- Critical Red count: stored Critical Red state in approved review snapshots.

## Missing Data

Missing approved review, assignment history, department, manager, location,
bonus rule/result, or sufficient trend history produces a named warning.
Missing values render as unavailable and do not become numeric zero.
