# KPI Bonus Versioning

## Version Sources

Every stored calculation includes:

- Stage 5 employee, team, and company review IDs and snapshot digests
- Stage 4 calculation and formula versions from each approved source
- Stage 3 assignment IDs and versions through the approved definition
- Bonus formula version
- Bonus calculation engine version
- Bonus rule code and version
- Weight-profile code and version
- Review date and calculation timestamp

## Configuration Changes

Published rule sets and weight profiles are immutable. A policy change requires
a new version with its own effective dates. Rule lookup uses the review date,
not the current date.

Team scope codes and labels are also versioned configuration, so department,
factory, sales, executive, or future organizational structures do not require
engine changes.

## Historical Protection

`KPIBonusCalculation`:

- Is one-to-one with its approved employee review
- Can be created only through the bonus service
- Rejects update, bulk update, bulk create, and delete operations
- Stores a canonical JSON result and SHA-256 digest
- Returns the existing verified record on repeated creation requests

Changing live KPI templates, assignments, employee departments, rule versions,
or formula code does not mutate a stored historical bonus result.

## Future Approval and Payroll Integration

Stage 6 stores eligibility and an estimate only. A later stage must create a
separate authorized approval record that references this immutable calculation.
Payment and payroll records must remain separate, require explicit permission,
and must never infer approval from `eligible=True`.
