# KPI Policy Approval Checklist

## Publication Order

1. KPI status settings
2. Role template versions
3. Bonus weight profile
4. Bonus rule set
5. Intelligence rule set
6. Notification rule

Publishing a notification rule does not install or run a scheduler.
Before publishing an overlapping successor, retire the predecessor with a
written reason. Publish a future-dated successor on its effective date unless
the predecessor already has an approved non-overlapping effective end.

## Common Review

For every policy:

- [ ] Correct policy type and version
- [ ] Effective start and optional end reviewed
- [ ] Draft content reviewed by business owner
- [ ] No employee name or private note embedded
- [ ] Creator and reviewer identified
- [ ] Written submission reason
- [ ] Written approval reason
- [ ] Approved by CEO or Super Admin
- [ ] Audit event present
- [ ] Historical version remains unchanged

## KPI Templates

- [ ] All 15 roles present
- [ ] Exact requested KPI names
- [ ] Active item weights total `100.00` per role
- [ ] Measurement and target are usable
- [ ] Review frequency and data source are correct
- [ ] Evidence and manager approval requirements reviewed
- [ ] Critical Red rule reviewed
- [ ] Bonus eligibility reviewed

## Status And Bonus

- [ ] Green `85.00-100.00`
- [ ] Yellow `70.00-84.99`
- [ ] Red `0.00-69.99`
- [ ] Critical Red override confirmed
- [ ] Bonus weights `60.00/25.00/15.00`
- [ ] Minimum score reviewed
- [ ] Attendance range reviewed
- [ ] Floor/cap approved or intentionally unset
- [ ] Currency approved
- [ ] No payroll or payment integration

## Intelligence And Notification

- [ ] Company-health weights total `100.00`
- [ ] Trend history and workload limits reviewed
- [ ] Data-quality behavior reviewed
- [ ] Reminder offsets reviewed
- [ ] Escalation recipients follow privacy rules
- [ ] Positive recognition choice reviewed
- [ ] Retry and batch limits reviewed
- [ ] No external provider configured

## Versioning

Never edit a Published or Retired source record. Create a Draft successor:

```bash
python3 manage.py manage_kpi_policy --action new-version \
  --policy-id ID --actor USERNAME --effective-date YYYY-MM-DD
```

Review and approve the new version independently.
