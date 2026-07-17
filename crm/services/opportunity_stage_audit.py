from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AutomationNotification,
    CostingHeader,
    Invoice,
    Opportunity,
    ProductionOrder,
    QuickCosting,
    Shipment,
)
from crm.services.costing_currency import currency_summary_rows, format_finance_money
from crm.services.opportunity_payment_stage import AWAITING_PAYMENT_STAGE, decimal_or_zero, invoice_open_balance


COMPLETED_STAGE_VALUES = {"Shipment Complete", "Closed Won"}
PRODUCTION_STAGE_VALUES = {"Production"}
NEGOTIATION_STAGE_VALUES = {"Negotiation"}
ARCHIVED_CATEGORY = "Archived"
PROPOSAL_CATEGORY = "Proposal"
NEGOTIATION_CATEGORY = "Negotiation"
AWAITING_PAYMENT_CATEGORY = "Awaiting Payment"
PRODUCTION_CATEGORY = "Production"
COMPLETED_CATEGORY = "Completed"
REPORT_CATEGORIES = (
    PROPOSAL_CATEGORY,
    NEGOTIATION_CATEGORY,
    AWAITING_PAYMENT_CATEGORY,
    PRODUCTION_CATEGORY,
    COMPLETED_CATEGORY,
    ARCHIVED_CATEGORY,
)


def _resolved_opportunity_ids_for_invoice(invoice):
    ids = set()
    for attr in ("opportunity", "quick_costing", "costing_header", "order"):
        record = getattr(invoice, attr, None)
        if not record:
            continue
        if attr == "opportunity" and getattr(record, "pk", None):
            ids.add(record.pk)
        elif getattr(record, "opportunity_id", None):
            ids.add(record.opportunity_id)
    return ids


def _current_category(opportunity):
    if opportunity.is_archived:
        return ARCHIVED_CATEGORY
    stage = opportunity.stage or ""
    if stage in COMPLETED_STAGE_VALUES:
        return COMPLETED_CATEGORY
    if stage in PRODUCTION_STAGE_VALUES:
        return PRODUCTION_CATEGORY
    if stage == AWAITING_PAYMENT_STAGE:
        return AWAITING_PAYMENT_CATEGORY
    if stage in NEGOTIATION_STAGE_VALUES:
        return NEGOTIATION_CATEGORY
    return PROPOSAL_CATEGORY


def _expected_category(*, opportunity, quotation_count, invoice_count, open_invoice_count, production_count, completed_shipment_count):
    if opportunity.is_archived:
        return ARCHIVED_CATEGORY
    if completed_shipment_count:
        return COMPLETED_CATEGORY
    if production_count:
        return PRODUCTION_CATEGORY
    if invoice_count and open_invoice_count:
        return AWAITING_PAYMENT_CATEGORY
    if quotation_count:
        return NEGOTIATION_CATEGORY
    return PROPOSAL_CATEGORY


def _warning(code, opportunity, message, *, severity="warning", target_url=""):
    return {
        "code": code,
        "severity": severity,
        "opportunity_id": getattr(opportunity, "pk", None),
        "opportunity_number": getattr(opportunity, "opportunity_id", "") or f"Opportunity {getattr(opportunity, 'pk', '')}",
        "stage": getattr(opportunity, "stage", ""),
        "message": message,
        "target_url": target_url,
    }


def _global_warning(code, message, *, severity="warning", record_id=None):
    return {
        "code": code,
        "severity": severity,
        "opportunity_id": None,
        "opportunity_number": f"Invoice {record_id}" if record_id else "",
        "stage": "",
        "message": message,
        "target_url": "",
    }


def build_opportunity_stage_audit():
    opportunities = list(
        Opportunity.objects.select_related("customer", "lead", "lead__customer")
        .order_by("id")
    )
    opportunity_ids = [opportunity.pk for opportunity in opportunities]

    quotation_counts = Counter()
    if opportunity_ids:
        for row in (
            CostingHeader.objects.filter(opportunity_id__in=opportunity_ids, is_archived=False)
            .exclude(quotation_number="")
            .values_list("opportunity_id", flat=True)
        ):
            quotation_counts[row] += 1
        for row in (
            QuickCosting.objects.filter(opportunity_id__in=opportunity_ids)
            .exclude(quotation_number="")
            .values_list("opportunity_id", flat=True)
        ):
            quotation_counts[row] += 1

    production_ids_by_opp = defaultdict(list)
    for production in ProductionOrder.objects.filter(
        opportunity_id__in=opportunity_ids,
        is_archived=False,
    ).only("id", "opportunity_id"):
        production_ids_by_opp[production.opportunity_id].append(production.pk)

    completed_shipments_by_opp = defaultdict(list)
    completed_shipments = (
        Shipment.objects.filter(status="delivered")
        | Shipment.objects.filter(delivered_at__isnull=False)
    )
    for shipment in completed_shipments.select_related("order").only("id", "opportunity_id", "order__opportunity_id"):
        opportunity_id = shipment.opportunity_id or getattr(shipment.order, "opportunity_id", None)
        if opportunity_id:
            completed_shipments_by_opp[opportunity_id].append(shipment.pk)

    invoices_by_opp = defaultdict(list)
    invoice_link_warnings = []
    invoices = (
        Invoice.objects.select_related(
            "opportunity",
            "quick_costing__opportunity",
            "costing_header__opportunity",
            "order__opportunity",
        )
        .filter(is_archived=False)
        .exclude(status__iexact="cancelled")
        .order_by("id")
    )
    for invoice in invoices:
        resolved_ids = _resolved_opportunity_ids_for_invoice(invoice)
        if not resolved_ids:
            invoice_link_warnings.append(
                _global_warning(
                    "invoice_link_missing",
                    f"{invoice.invoice_number or 'Invoice ' + str(invoice.pk)} has no resolvable opportunity link.",
                    severity="critical",
                    record_id=invoice.pk,
                )
            )
            continue
        if len(resolved_ids) > 1:
            invoice_link_warnings.append(
                _global_warning(
                    "invoice_link_conflict",
                    f"{invoice.invoice_number or 'Invoice ' + str(invoice.pk)} points to multiple opportunity IDs: {', '.join(str(pk) for pk in sorted(resolved_ids))}.",
                    severity="critical",
                    record_id=invoice.pk,
                )
            )
        for opportunity_id in resolved_ids:
            if opportunity_id in opportunity_ids:
                invoices_by_opp[opportunity_id].append(invoice)

    rows = []
    warnings = list(invoice_link_warnings)
    category_counts = Counter({category: 0 for category in REPORT_CATEGORIES})
    warning_counts = Counter()

    for opportunity in opportunities:
        invoices = invoices_by_opp.get(opportunity.pk, [])
        open_invoices = [invoice for invoice in invoices if invoice_open_balance(invoice) > 0]
        open_balance_totals = defaultdict(lambda: {"amount": Decimal("0")})
        for invoice in open_invoices:
            currency = (invoice.currency or "CAD").upper()
            open_balance_totals[currency]["amount"] += invoice_open_balance(invoice)
        balance_rows = currency_summary_rows(open_balance_totals)
        balance_display = " / ".join(
            format_finance_money(row["amount"], row["currency"]) for row in balance_rows
        ) or "-"

        quotation_count = quotation_counts[opportunity.pk]
        invoice_count = len({invoice.pk for invoice in invoices})
        open_invoice_count = len({invoice.pk for invoice in open_invoices})
        production_count = len(production_ids_by_opp.get(opportunity.pk, []))
        completed_shipment_count = len(completed_shipments_by_opp.get(opportunity.pk, []))
        current_category = _current_category(opportunity)
        expected_category = _expected_category(
            opportunity=opportunity,
            quotation_count=quotation_count,
            invoice_count=invoice_count,
            open_invoice_count=open_invoice_count,
            production_count=production_count,
            completed_shipment_count=completed_shipment_count,
        )
        category_counts[expected_category] += 1
        try:
            target_url = reverse("opportunity_detail", args=[opportunity.pk])
        except Exception:
            target_url = ""

        if not opportunity.customer_id:
            warnings.append(
                _warning(
                    "missing_customer",
                    opportunity,
                    "Opportunity has no direct customer link.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and open_invoice_count and not production_count and opportunity.stage != AWAITING_PAYMENT_STAGE:
            warnings.append(
                _warning(
                    "invoice_stage_incorrect",
                    opportunity,
                    "Invoice exists with outstanding balance, but stage is not Awaiting Payment.",
                    severity="critical",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == AWAITING_PAYMENT_STAGE and not (open_invoice_count and not production_count):
            warnings.append(
                _warning(
                    "awaiting_payment_invalid",
                    opportunity,
                    "Stage is Awaiting Payment but the opportunity does not have an open invoice balance without production.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and production_count and current_category not in {PRODUCTION_CATEGORY, COMPLETED_CATEGORY}:
            warnings.append(
                _warning(
                    "production_stage_incorrect",
                    opportunity,
                    "Production order exists, but opportunity stage is not Production or Completed.",
                    severity="critical",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == "Production" and not production_count:
            warnings.append(
                _warning(
                    "production_link_missing",
                    opportunity,
                    "Opportunity is marked Production but has no linked production order.",
                    severity="critical",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and completed_shipment_count and current_category != COMPLETED_CATEGORY:
            warnings.append(
                _warning(
                    "completed_stage_incorrect",
                    opportunity,
                    "Completed shipment exists, but opportunity is not marked Completed.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == "Proposal" and (quotation_count or invoice_count):
            warnings.append(
                _warning(
                    "proposal_has_downstream_records",
                    opportunity,
                    "Proposal-stage opportunity has a quotation or invoice.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if not opportunity.is_archived and opportunity.stage == "Negotiation" and invoice_count:
            warnings.append(
                _warning(
                    "negotiation_has_invoice",
                    opportunity,
                    "Negotiation-stage opportunity already has an invoice.",
                    severity="warning",
                    target_url=target_url,
                )
            )
        if production_count > 1:
            warnings.append(
                _warning(
                    "duplicate_production_links",
                    opportunity,
                    f"Opportunity has {production_count} linked production orders.",
                    severity="critical",
                    target_url=target_url,
                )
            )

        rows.append(
            {
                "id": opportunity.pk,
                "opportunity_number": opportunity.opportunity_id,
                "customer": (
                    getattr(opportunity.customer, "account_brand", "")
                    or getattr(opportunity.customer, "contact_name", "")
                    or ""
                ),
                "current_stage": opportunity.stage,
                "current_category": current_category,
                "expected_category": expected_category,
                "quotation_count": quotation_count,
                "invoice_count": invoice_count,
                "open_invoice_count": open_invoice_count,
                "outstanding_balance": balance_display,
                "production_count": production_count,
                "completed_shipment_count": completed_shipment_count,
                "archived": opportunity.is_archived,
                "customer_missing": not bool(opportunity.customer_id),
                "target_url": target_url,
            }
        )

    for warning in warnings:
        warning_counts[warning["code"]] += 1

    broken_opportunity_ids = {
        warning["opportunity_id"]
        for warning in warnings
        if warning.get("opportunity_id")
    }
    broken_production_links = sum(
        warning_counts[code]
        for code in ("production_stage_incorrect", "production_link_missing", "duplicate_production_links")
    )
    broken_invoice_links = sum(
        warning_counts[code]
        for code in ("invoice_link_missing", "invoice_link_conflict")
    )
    metrics = {
        "total_opportunities": len(rows),
        "workflow_errors": len(warnings),
        "broken_opportunities": len(broken_opportunity_ids),
        "broken_production_links": broken_production_links,
        "broken_invoice_links": broken_invoice_links,
        "category_counts": dict(category_counts),
        "warning_counts": dict(warning_counts),
    }
    return {
        "generated_at": timezone.now(),
        "rows": rows,
        "warnings": warnings,
        "metrics": metrics,
    }


def build_workflow_integrity_dashboard_metrics():
    opportunity_table = Opportunity._meta.db_table
    production_table = ProductionOrder._meta.db_table
    invoice_table = Invoice._meta.db_table
    quick_table = QuickCosting._meta.db_table
    costing_table = CostingHeader._meta.db_table
    shipment_table = Shipment._meta.db_table
    sql = f"""
        WITH prod_by_opp AS (
            SELECT opportunity_id AS opp_id, COUNT(*) AS prod_count
            FROM {production_table}
            WHERE is_archived = 0 AND opportunity_id IS NOT NULL
            GROUP BY opportunity_id
        ),
        invoice_base AS (
            SELECT id, currency, total_amount, paid_amount, opportunity_id, quick_costing_id, costing_header_id, order_id
            FROM {invoice_table}
            WHERE is_archived = 0 AND LOWER(status) <> 'cancelled'
        ),
        invoice_links AS (
            SELECT id AS invoice_id, opportunity_id AS opp_id, currency, total_amount - paid_amount AS balance
            FROM invoice_base
            WHERE opportunity_id IS NOT NULL
            UNION ALL
            SELECT inv.id AS invoice_id, quick.opportunity_id AS opp_id, inv.currency, inv.total_amount - inv.paid_amount AS balance
            FROM invoice_base inv
            JOIN {quick_table} quick ON quick.id = inv.quick_costing_id
            WHERE quick.opportunity_id IS NOT NULL
            UNION ALL
            SELECT inv.id AS invoice_id, costing.opportunity_id AS opp_id, inv.currency, inv.total_amount - inv.paid_amount AS balance
            FROM invoice_base inv
            JOIN {costing_table} costing ON costing.id = inv.costing_header_id
            WHERE costing.opportunity_id IS NOT NULL
            UNION ALL
            SELECT inv.id AS invoice_id, prod.opportunity_id AS opp_id, inv.currency, inv.total_amount - inv.paid_amount AS balance
            FROM invoice_base inv
            JOIN {production_table} prod ON prod.id = inv.order_id
            WHERE prod.opportunity_id IS NOT NULL
        ),
        distinct_invoice_links AS (
            SELECT DISTINCT invoice_id, opp_id, currency, balance
            FROM invoice_links
            WHERE opp_id IS NOT NULL
        ),
        invoices_by_opp AS (
            SELECT
                opp_id,
                COUNT(DISTINCT invoice_id) AS invoice_count,
                COUNT(DISTINCT CASE WHEN balance > 0 THEN invoice_id END) AS open_invoice_count
            FROM distinct_invoice_links
            GROUP BY opp_id
        ),
        awaiting_links AS (
            SELECT
                opp.id AS opportunity_id,
                COALESCE(opp.customer_id, opp.id) AS customer_key,
                links.currency AS currency,
                links.balance AS balance
            FROM distinct_invoice_links links
            JOIN {opportunity_table} opp ON opp.id = links.opp_id
            LEFT JOIN prod_by_opp prod ON prod.opp_id = opp.id
            WHERE links.balance > 0
              AND opp.is_archived = 0
              AND opp.stage = %s
              AND COALESCE(prod.prod_count, 0) = 0
        ),
        awaiting_summary AS (
            SELECT
                COUNT(DISTINCT opportunity_id) AS opportunity_count,
                COUNT(DISTINCT customer_key) AS customer_count
            FROM awaiting_links
        ),
        awaiting_by_currency AS (
            SELECT
                currency,
                SUM(balance) AS amount
            FROM awaiting_links
            GROUP BY currency
        ),
        quote_links AS (
            SELECT opportunity_id AS opp_id
            FROM {costing_table}
            WHERE is_archived = 0 AND COALESCE(quotation_number, '') <> ''
            UNION ALL
            SELECT opportunity_id AS opp_id
            FROM {quick_table}
            WHERE COALESCE(quotation_number, '') <> ''
        ),
        quotes_by_opp AS (
            SELECT opp_id, COUNT(*) AS quote_count
            FROM quote_links
            WHERE opp_id IS NOT NULL
            GROUP BY opp_id
        ),
        delivered_by_opp AS (
            SELECT opp_id, COUNT(*) AS delivered_count
            FROM (
                SELECT ship.id, COALESCE(ship.opportunity_id, prod.opportunity_id) AS opp_id
                FROM {shipment_table} ship
                LEFT JOIN {production_table} prod ON prod.id = ship.order_id
                WHERE (ship.status = 'delivered' OR ship.delivered_at IS NOT NULL)
            ) delivered
            WHERE opp_id IS NOT NULL
            GROUP BY opp_id
        ),
        invoice_conflicts AS (
            SELECT invoice_id
            FROM distinct_invoice_links
            GROUP BY invoice_id
            HAVING COUNT(DISTINCT opp_id) > 1
        ),
        invoice_broken AS (
            SELECT
                (
                    SELECT COUNT(*)
                    FROM invoice_base inv
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM distinct_invoice_links links
                        WHERE links.invoice_id = inv.id
                    )
                ) + (SELECT COUNT(*) FROM invoice_conflicts) AS broken_invoice_links
        ),
        opp_flags AS (
            SELECT
                opp.id,
                CASE WHEN opp.customer_id IS NULL THEN 1 ELSE 0 END AS missing_customer,
                CASE WHEN opp.is_archived = 0 AND COALESCE(inv.open_invoice_count, 0) > 0 AND COALESCE(prod.prod_count, 0) = 0 AND opp.stage <> %s THEN 1 ELSE 0 END AS invoice_stage_incorrect,
                CASE WHEN opp.is_archived = 0 AND opp.stage = %s AND NOT (COALESCE(inv.open_invoice_count, 0) > 0 AND COALESCE(prod.prod_count, 0) = 0) THEN 1 ELSE 0 END AS awaiting_payment_invalid,
                CASE WHEN opp.is_archived = 0 AND COALESCE(prod.prod_count, 0) > 0 AND opp.stage NOT IN ('Production', 'Shipment Complete', 'Closed Won') THEN 1 ELSE 0 END AS production_stage_incorrect,
                CASE WHEN opp.is_archived = 0 AND opp.stage = 'Production' AND COALESCE(prod.prod_count, 0) = 0 THEN 1 ELSE 0 END AS production_link_missing,
                CASE WHEN opp.is_archived = 0 AND COALESCE(delivered.delivered_count, 0) > 0 AND opp.stage NOT IN ('Shipment Complete', 'Closed Won') THEN 1 ELSE 0 END AS completed_stage_incorrect,
                CASE WHEN opp.is_archived = 0 AND opp.stage = 'Proposal' AND (COALESCE(inv.invoice_count, 0) > 0 OR COALESCE(quote.quote_count, 0) > 0) THEN 1 ELSE 0 END AS proposal_has_downstream_records,
                CASE WHEN opp.is_archived = 0 AND opp.stage = 'Negotiation' AND COALESCE(inv.invoice_count, 0) > 0 THEN 1 ELSE 0 END AS negotiation_has_invoice,
                CASE WHEN COALESCE(prod.prod_count, 0) > 1 THEN 1 ELSE 0 END AS duplicate_production_links
            FROM {opportunity_table} opp
            LEFT JOIN prod_by_opp prod ON prod.opp_id = opp.id
            LEFT JOIN invoices_by_opp inv ON inv.opp_id = opp.id
            LEFT JOIN quotes_by_opp quote ON quote.opp_id = opp.id
            LEFT JOIN delivered_by_opp delivered ON delivered.opp_id = opp.id
        )
        SELECT
            COALESCE(SUM(
                missing_customer
                + invoice_stage_incorrect
                + awaiting_payment_invalid
                + production_stage_incorrect
                + production_link_missing
                + completed_stage_incorrect
                + proposal_has_downstream_records
                + negotiation_has_invoice
                + duplicate_production_links
            ), 0) + (SELECT broken_invoice_links FROM invoice_broken) AS workflow_errors,
            COALESCE(SUM(
                CASE WHEN (
                    missing_customer
                    + invoice_stage_incorrect
                    + awaiting_payment_invalid
                    + production_stage_incorrect
                    + production_link_missing
                    + completed_stage_incorrect
                    + proposal_has_downstream_records
                    + negotiation_has_invoice
                    + duplicate_production_links
                ) > 0 THEN 1 ELSE 0 END
            ), 0) AS broken_opportunities,
            COALESCE(SUM(production_stage_incorrect + production_link_missing + duplicate_production_links), 0) AS broken_production_links,
            (SELECT broken_invoice_links FROM invoice_broken) AS broken_invoice_links,
            COALESCE((SELECT opportunity_count FROM awaiting_summary), 0) AS awaiting_payment_count,
            COALESCE((SELECT customer_count FROM awaiting_summary), 0) AS awaiting_payment_customer_count,
            COALESCE((SELECT SUM(amount) FROM awaiting_by_currency WHERE currency = 'CAD'), 0) AS awaiting_cad,
            COALESCE((SELECT SUM(amount) FROM awaiting_by_currency WHERE currency = 'USD'), 0) AS awaiting_usd,
            COALESCE((SELECT SUM(amount) FROM awaiting_by_currency WHERE currency = 'BDT'), 0) AS awaiting_bdt
        FROM opp_flags
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [AWAITING_PAYMENT_STAGE, AWAITING_PAYMENT_STAGE, AWAITING_PAYMENT_STAGE])
        row = cursor.fetchone() or [0] * 9
    totals = {
        "CAD": {"amount": decimal_or_zero(row[6])},
        "USD": {"amount": decimal_or_zero(row[7])},
        "BDT": {"amount": decimal_or_zero(row[8])},
    }
    totals = {currency: values for currency, values in totals.items() if values["amount"]}
    rows = currency_summary_rows(totals)
    for currency_row in rows:
        currency_row["display"] = format_finance_money(currency_row["amount"], currency_row["currency"])
    display = " / ".join(currency_row["display"] for currency_row in rows) or "-"
    return {
        "workflow_errors": int(row[0] or 0),
        "broken_opportunities": int(row[1] or 0),
        "broken_production_links": int(row[2] or 0),
        "broken_invoice_links": int(row[3] or 0),
        "awaiting_payment_count": int(row[4] or 0),
        "awaiting_payment_customer_count": int(row[5] or 0),
        "awaiting_payment_rows": rows,
        "awaiting_payment_display": display,
    }


def render_opportunity_stage_audit_markdown(audit):
    generated_at = audit["generated_at"].strftime("%Y-%m-%d %H:%M:%S %Z")
    metrics = audit["metrics"]
    lines = [
        "# Opportunity Stage Audit Report",
        "",
        f"Generated at: {generated_at}",
        "",
        "## Summary",
        "",
        f"- Total opportunities: {metrics['total_opportunities']}",
        f"- Workflow errors: {metrics['workflow_errors']}",
        f"- Broken opportunities: {metrics['broken_opportunities']}",
        f"- Broken production links: {metrics['broken_production_links']}",
        f"- Broken invoice links: {metrics['broken_invoice_links']}",
        "",
        "## Classification Counts",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
    ]
    for category in REPORT_CATEGORIES:
        lines.append(f"| {category} | {metrics['category_counts'].get(category, 0)} |")
    lines.extend(["", "## Warning Counts", "", "| Warning | Count |", "| --- | ---: |"])
    if metrics["warning_counts"]:
        for code, count in sorted(metrics["warning_counts"].items()):
            lines.append(f"| {code} | {count} |")
    else:
        lines.append("| None | 0 |")

    lines.extend(
        [
            "",
            "## Warnings",
            "",
            "| Code | Severity | Opportunity | Current Stage | Message |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if audit["warnings"]:
        for warning in audit["warnings"]:
            lines.append(
                "| {code} | {severity} | {opportunity} | {stage} | {message} |".format(
                    code=warning["code"],
                    severity=warning["severity"],
                    opportunity=warning["opportunity_number"] or "-",
                    stage=warning["stage"] or "-",
                    message=warning["message"].replace("|", "\\|"),
                )
            )
    else:
        lines.append("| None | - | - | - | No warnings found. |")

    lines.extend(
        [
            "",
            "## Opportunity Classification",
            "",
            "| ID | Opportunity | Customer | Current Stage | Current Classification | Expected Classification | Quotations | Invoices | Open Invoices | Outstanding Balance | Production Orders | Completed Shipments | Archived |",
            "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for row in audit["rows"]:
        lines.append(
            "| {id} | {opportunity_number} | {customer} | {current_stage} | {current_category} | {expected_category} | {quotation_count} | {invoice_count} | {open_invoice_count} | {outstanding_balance} | {production_count} | {completed_shipment_count} | {archived} |".format(
                **{
                    key: str(value).replace("|", "\\|")
                    for key, value in row.items()
                    if key
                    in {
                        "id",
                        "opportunity_number",
                        "customer",
                        "current_stage",
                        "current_category",
                        "expected_category",
                        "quotation_count",
                        "invoice_count",
                        "open_invoice_count",
                        "outstanding_balance",
                        "production_count",
                        "completed_shipment_count",
                        "archived",
                    }
                }
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_opportunity_stage_audit_report(path, audit=None):
    audit = audit or build_opportunity_stage_audit()
    output_path = Path(path)
    output_path.write_text(render_opportunity_stage_audit_markdown(audit), encoding="utf-8")
    return output_path


def sync_opportunity_stage_audit_notification(audit):
    metrics = audit["metrics"]
    source_key = "opportunity-stage-audit:summary:ceo"
    queryset = AutomationNotification.objects.filter(source_key=source_key)
    if metrics["workflow_errors"] <= 0:
        queryset.update(is_resolved=True, resolved_at=timezone.now())
        return {"active": False, "source_key": source_key}

    content_type = ContentType.objects.get_for_model(Opportunity, for_concrete_model=False)
    first_warning = next((warning for warning in audit["warnings"] if warning.get("opportunity_id")), None)
    record_object_id = first_warning["opportunity_id"] if first_warning else None
    target_url = first_warning["target_url"] if first_warning else reverse("ceo_dashboard")
    priority = "critical" if metrics["broken_production_links"] or metrics["broken_invoice_links"] else "high"
    AutomationNotification.objects.update_or_create(
        source_key=source_key,
        defaults={
            "rule": None,
            "rule_type": "general",
            "notification_type": "general",
            "title": "Opportunity workflow integrity warning",
            "message": (
                f"{metrics['workflow_errors']} workflow warning(s) found across "
                f"{metrics['broken_opportunities']} opportunity record(s). "
                f"Production link warnings: {metrics['broken_production_links']}. "
                f"Invoice link warnings: {metrics['broken_invoice_links']}."
            ),
            "priority": priority,
            "is_read": False,
            "is_resolved": False,
            "resolved_at": None,
            "record_content_type": content_type if record_object_id else None,
            "record_object_id": record_object_id,
            "record_label": first_warning["opportunity_number"] if first_warning else "Opportunity Stage Audit",
            "target_url": target_url,
            "assigned_user": None,
            "assigned_role": "CEO",
            "due_date": timezone.localdate(),
        },
    )
    return {"active": True, "source_key": source_key}
