from collections import Counter

from crm.services.financial_exception_review import exception_identifier
from crm.services.financial_historical_reconciliation import build_financial_historical_exception_plan


CLASS_AUTO_FIX = "Auto fix"
CLASS_CEO_REVIEW = "Requires CEO review"
CLASS_IGNORE = "Ignore"
CLASS_ARCHIVE = "Archive"


def classify_exception(exception):
    approved_duplicate = exception.record == "Invoice INV00019" and (
        "exception payment_exceeds_unallocated_balance" in exception.current_value
        or "exception tracked_payments_exceed_stored_paid" in exception.current_value
    )
    if approved_duplicate:
        return CLASS_AUTO_FIX
    return CLASS_CEO_REVIEW


def build_exception_classification_rows():
    return [
        {
            "exception_id": exception_identifier(exception),
            "severity": exception.severity,
            "area": exception.area,
            "record": exception.record,
            "classification": classify_exception(exception),
            "reason": exception.reason,
            "recommended_action": exception.recommended_action,
        }
        for exception in build_financial_historical_exception_plan()
    ]


def render_exception_classification_report(rows):
    counts = Counter(row["classification"] for row in rows)
    lines = [
        "# Finance Historical Exception Classification Report",
        "",
        "This report classifies the current historical exception plan. It does not guess accounting values,",
        "approve missing evidence, create journals, or change monetary history.",
        "",
        "## Summary",
        "",
        f"- Total: {len(rows)}",
        f"- {CLASS_AUTO_FIX}: {counts[CLASS_AUTO_FIX]}",
        f"- {CLASS_CEO_REVIEW}: {counts[CLASS_CEO_REVIEW]}",
        f"- {CLASS_IGNORE}: {counts[CLASS_IGNORE]}",
        f"- {CLASS_ARCHIVE}: {counts[CLASS_ARCHIVE]}",
        "",
        "The two Auto fix rows are the previously CEO-approved duplicate Payment 12 / AccountingEntry 143",
        "correction. Ignore and Archive remain zero because those decisions require explicit CEO evidence.",
        "",
        "## Exceptions",
        "",
        "| Exception | Severity | Area | Record | Classification | Reason | Recommended action |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        values = [
            row["exception_id"],
            row["severity"],
            row["area"],
            row["record"],
            row["classification"],
            row["reason"],
            row["recommended_action"],
        ]
        escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")
    lines.append("")
    return "\n".join(lines)
