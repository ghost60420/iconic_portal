# Finance Field Requirement Matrix

This matrix applies to the existing Finance Operations Center forms. It does not change country locking, permissions, approval authority, posting rules, or accounting calculations.

## Classification

- **Required for draft**: minimum accounting data needed to preserve a valid Finance operation record. The same fields remain required when submitting.
- **Required for approval**: conditional information required by the selected transaction type or status before submission.
- **Optional**: may be blank for both Save Draft and Submit for Approval. Missing evidence is shown to the approver but does not block submission.

## Fields Shared by Every Workflow

| Field | Save Draft | Submit for Approval | Notes |
| --- | --- | --- | --- |
| Transaction type | Required | Required | Set by the selected existing workflow. |
| Date | Required | Required | Uses the transaction or workflow date. |
| Business side | Required | Required | Canada/Bangladesh access and locking are unchanged. |
| Currency | Required | Required | Must agree with the selected side, party, account, or source record where applicable. |
| Reference number | Optional | Optional | Duplicate controls still run when a reference is entered. |
| Purpose | Optional | Optional | No required star. |
| Notes | Optional | Optional | No required star. |
| Receipt / supporting document | Optional | Optional | Missing evidence triggers a confirmation and is visible to the approver. |
| Exchange rates | Optional | Conditional | Uses approved rates when available; manual values remain under Advanced Details. |

## Workflow-Specific Fields

The “Required for draft” column lists the existing minimum accounting fields needed to save a structurally valid record. The “Required for approval” column lists additional conditional checks applied by the workflow. All fields in the “Optional” column have no required star.

| Workflow | Required for draft | Required for approval / conditional | Optional |
| --- | --- | --- | --- |
| Customer Payment | Customer; invoice; amount; payment method; payment account | Customer must own invoice; invoice, account, side, and currency must agree; excess requires customer-credit confirmation | Credit exception controls; reference; purpose; notes; receipt |
| Customer Refund | Adjustment type; customer; amount; reason | Refund account; invoice when refund is invoice-based; side/currency agreement | Invoice when not invoice-based; reference; purpose; notes; receipt |
| Customer Credit Note | Adjustment type; customer; invoice; amount; reason | Amount cannot exceed invoice outstanding; side/currency agreement | Reference; purpose; notes; receipt |
| Customer Credit | Adjustment type; customer; amount; reason; deposit account | Deposit account, side, and currency must agree | Invoice; reference; purpose; notes; receipt |
| Supplier Bill | Supplier; bill date; due date; subtotal; total; expense category | Total must equal subtotal plus tax; supplier side/currency must agree | Tax (defaults to zero); bill/reference number; department; production order; customer; purpose; notes; receipt |
| Supplier Payment | Supplier; supplier bill; amount; payment method; payment account | Supplier must own bill; amount cannot exceed outstanding; account, side, and currency must agree | Reference; purpose; notes; receipt |
| Company Expense | Vendor or vendor name; category; subtotal; tax; total; payment status | Paid: payment account required. Unpaid: configured supplier and due date required. Total must equal subtotal plus tax | Department; production order; payment method; recurring; reference; purpose; notes; receipt |
| Utility Bill | Utility type; vendor; bill date; due date; amount; location; payment status | Paid: payment account required. Utility account mapping and vendor side must be valid | Billing period; meter/account number; reference; purpose; notes; receipt |
| Payroll | Payroll month; employee or department; payroll type; gross; net paid; payment account | Gross must equal deductions plus net; account, employee, side, and currency must agree | Deductions (defaults to zero); employer cost (defaults to zero); reference; purpose; notes; receipt |
| Production Cost | Production order; cost category; supplier; estimated cost; actual cost; payment status | Paid: payment account required. Production order, supplier, side, and currency must agree | Payment method; reference; purpose; notes; receipt |
| Factory Daily Cost | Quick Costing; daily cost default; estimated days; estimated revenue; other estimated cost | Quick Costing, saved BDT daily-rate snapshot, and Bangladesh side must agree; actual revenue/cost entered as a pair | Actual days; actual revenue; other actual cost; margin controls; delay reason; reference; purpose; notes; receipt |
| Bank Deposit | Amount; destination account | Source and destination accounts required by the existing movement workflow; account type, side, and currency must agree | Cross-currency destination amount/rates when not applicable; reference; purpose; notes; receipt |
| Bank Withdrawal | Amount; source account; destination account | Source must be bank and destination cash; side/currency must agree | Cross-currency destination amount/rates when not applicable; reference; purpose; notes; receipt |
| Cash Deposit | Amount; destination account | Source and destination accounts required by the existing movement workflow; destination must be cash; side/currency must agree | Cross-currency destination amount/rates when not applicable; reference; purpose; notes; receipt |
| Cash Withdrawal | Amount; source account; destination account | Source must be cash; side/currency must agree | Cross-currency destination amount/rates when not applicable; reference; purpose; notes; receipt |
| Money Transfer | Amount; source account; destination account | Accounts must differ; side/currency rules apply; cross-currency transfers require a reconciled destination amount/rate | Destination amount/rates for same-currency transfer; reference; purpose; notes; receipt |
| Bank Fee | Amount; charged account | Account, side, and currency must agree | Destination account; destination amount/rates; reference; purpose; notes; receipt |
| Processor Fee | Amount; charged account | Account, side, and currency must agree | Destination account; destination amount/rates; reference; purpose; notes; receipt |
| Owner Investment | Party; amount; payment account | Incoming account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Owner Withdrawal | Party; amount; payment account | Funding account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Loan Received | Party; amount; payment account | Incoming account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Loan Principal Payment | Party; amount; payment account | Funding account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Loan Interest Payment | Party; amount; payment account | Funding account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Shareholder Advance | Party; amount; payment account | Incoming account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Shareholder Repayment | Party; amount; payment account | Funding account, side, and currency must agree | Reason; reference; purpose; notes; receipt |
| Asset Purchase | Asset name; asset type; supplier; amount; payment account; useful life | Supplier, payment account, side, and currency must agree | Department; serial number; reference; purpose; notes; receipt |
| Inventory Adjustment | Adjustment type; inventory item; quantity; unit cost; direction | Purchase: supplier required. Reduction: current cost and available quantity controls apply | Supplier for non-purchase adjustments; reason; reference; purpose; notes; receipt |

## State and Posting Rules

| State | General Ledger | AR / AP | Bank / Cash | P&L / Cash Flow |
| --- | --- | --- | --- | --- |
| Draft | No change | No change | No change | No change |
| Pending / More Information Required | No change | No change | No change | No change |
| Approved, not posted | No change | No change | No change | No change |
| Posted | Existing Financial Core behavior | Existing Financial Core behavior | Existing Financial Core behavior | Existing Financial Core behavior |

Approvers can approve, return for more information, or reject a transaction with missing evidence. Existing separation-of-duties, high-risk, account-mapping, permissions, and posting controls remain active.
