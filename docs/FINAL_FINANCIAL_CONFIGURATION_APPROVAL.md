# Financial configuration: authorized periods and pending account confirmation

Updated 12 October 2026 for `codex/final-crm-integration`, following explicit CEO authorization in the final financial integration request. October periods were opened at 22:48:59 UTC through the existing models and Financial Core audit helper. No receiving account, ledger account, opening balance, historical period, payment, or journal was changed. The account findings below were rechecked against the post-change production backup.

## October periods

August 2026 and September 2026 remain OPEN and global (`side=''`), unchanged. The audit found **zero overlapping period pairs** and **zero October conflicts** before opening the two country-specific periods. Country-specific CA and BD periods can share dates because they apply to separate countries.

The authorized configuration is now applied:

| Name | Country / side | Start | End | Current state |
| --- | --- | --- | --- | --- |
| October 2026 — Canada | CA | 2026-10-01 | 2026-10-31 | OPEN |
| October 2026 — Bangladesh | BD | 2026-10-01 | 2026-10-31 | OPEN |

Canada is period **3**, Bangladesh is period **4**. Approval actor is existing CEO/Super Admin user **11 (Hossain)**. Immutable Financial Core audit events **29** and **30** record action `PERIOD_OPENED`, the exact configuration, execution timestamp, and reference `CEO-AUTH-OCT2026-CA-BD-20261012`. Both periods and audits were committed in one transaction after checking all existing periods/audits and fingerprints of the other 207 application tables. All historical rows remained unchanged. All 62 country/day combinations resolve correctly through the existing posting-period service. No closed period was reopened. Code deployment still requires separate final authorization.

## Receiving account findings

| Receiving record | Actual mapping | Finding / required action |
| --- | --- | --- |
| 1 — SCO, BANK, CA, CAD, active | GL 3 / 1020, Canadian Bank Accounts, ASSET | Structurally valid. Reuse for Canadian Bank Transfer and E Transfer if Finance/CEO confirms this is the actual receiving instrument. |
| 2 — SCO, BANK, CA, CAD, active | GL 58 / 6040, Bangladesh Salaries, OPERATING_EXPENSE | Invalid for customer receipts. Confirm whether it is a duplicate or a separate real bank instrument; its masked reference differs from record 1. Do not change GL 6040 into an asset. |
| 3 — Taptap, MOBILE, BD, BDT, active | GL 50 / 5100, Production Shipping, COGS | Invalid for these customer receipt methods. Two POSTED account transfers (operations 4 and 5) reference this record. Preserve it and its historical journals pending a separately authorized accounting review. Do not relabel it as a bank or change GL 5100 into an asset. |

The payment release rejects records 2 and 3 as receiving accounts. Filtering them from customer receipts does not rewrite historical transfers.

## Existing asset accounts available for confirmed configuration

| Purpose | Existing active asset GL | Prepared configuration / confirmation needed |
| --- | --- | --- |
| Canadian Bank | 3 / 1020 — Canadian Bank Accounts (`CANADIAN_BANK`) | Reuse receiving record 1 after confirming instrument identity. No new record required for E Transfer. |
| E Transfer | Same confirmed Canadian BANK receiving record | Select E Transfer as the payment method; no duplicate bank or ledger record. |
| Bangladesh Bank | 4 / 1030 — Bangladesh Bank Accounts (`BANGLADESH_BANK`) | GL exists and has no receiving record. Confirm the real bank/instrument name and masked reference, BD/BDT ownership, active use, and explicit mapping approval before configuring a BANK receiving record. |
| PayPal | 5 / 1040 — PayPal and Payment Accounts (`PAYMENT_ACCOUNTS`) | GL exists and has no receiving record. Confirm the actual PayPal business account, country, settlement currency, masked reference, and mapping approval before configuring a PAYPAL receiving record. |
| Cash | 2 / 1010 — Cash in Hand (`CASH_IN_HAND`) | GL exists and has no receiving record. Confirm country, currency, cash custodian/location, and mapping approval before configuring a CASH receiving record. No opening balance will be invented. |

`CashBankAccount.gl_account` is **one-to-one**. GL 1020 is already assigned to receiving record 1, so record 2 cannot simply be mapped to that same GL. If record 2 is a distinct bank, or cash/PayPal must operate in more than one country/currency, Finance must approve distinct existing asset subaccounts or an explicit chart-of-accounts configuration. Do not reuse one ledger across multiple receiving records by bypassing this constraint.

Bank details already present in invoice settings must be confirmed against the actual instruments; invoice instructions alone are not proof of a valid receiving-account mapping. No private bank details are copied into this report.

## Exact outstanding information

1. Confirmation that receiving record 1 is the Canadian receiving bank and supports E Transfer.
2. Whether receiving record 2 is a duplicate or a distinct instrument, and its approved asset GL if distinct.
3. Confirmed Bangladesh bank name/instrument and masked reference for GL 1030.
4. Confirmed PayPal instrument, country, settlement currency, and mapping to GL 1040.
5. Confirmed cash country/currency/custodian and mapping to GL 1010; approved separate asset mappings if cash is required in both CA and BD.

Any accounting review of record 3's historical transfers is a separate follow-up. This release will not amend those records, and that review is not a prerequisite to configuring other confirmed valid receiving instruments.

Until account details are confirmed and the resulting configuration passes the existing validations, the release is **NOT READY FOR FINAL DEPLOYMENT**. The explicit October-period authorization has been applied; it is not authorization to guess receiving-account ownership/mappings or to deploy code.
