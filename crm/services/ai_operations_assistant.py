from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from crm.models import (
    AutomationNotification,
    CostingHeader,
    InventoryItem,
    Invoice,
    ProductionOrder,
    ProductionStage,
    Shipment,
)
from crm.permissions import get_access


DONE_PRODUCTION_STATUSES = ["done", "closed_won", "closed_lost"]


def _decimal(value):
    if value in (None, ""):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value, currency="CAD"):
    amount = _decimal(value)
    return f"{currency} {amount:,.2f}"


def _safe_reverse(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


def _customer_label(customer):
    if not customer:
        return "No customer"
    return getattr(customer, "account_brand", "") or getattr(customer, "contact_name", "") or str(customer)


def _invoice_label(invoice):
    return f"{invoice.invoice_number} - {_customer_label(getattr(invoice, 'customer', None))}"


def _production_label(order):
    return getattr(order, "order_code", "") or getattr(order, "title", "") or f"Production {order.pk}"


def _costing_label(costing):
    quote = getattr(costing, "quotation_number", "") or f"Costing {costing.pk}"
    style = getattr(costing, "style_name", "") or getattr(costing, "product_type", "")
    return f"{quote}{' - ' + style if style else ''}"


def _inventory_label(item):
    return getattr(item, "name", "") or f"Inventory {item.pk}"


def _tone_for_count(value, warning=1, bad=3):
    value = int(value or 0)
    if value >= bad:
        return "bad"
    if value >= warning:
        return "warn"
    return "good"


def _access_flags(user):
    flags = {
        "can_view_page": False,
        "can_view_ceo_tools": False,
        "can_view_financials": False,
        "can_view_profit": False,
        "can_view_quotations": False,
        "can_view_production": False,
        "can_view_shipping": False,
        "can_view_inventory": False,
        "can_view_lifecycle": False,
    }
    if not user or not getattr(user, "is_authenticated", False):
        return flags
    if getattr(user, "is_superuser", False):
        return {key: True for key in flags}

    try:
        access = get_access(user)
    except (OperationalError, ProgrammingError):
        return flags

    can_ceo = bool(getattr(access, "can_view_ceo_tools", False))
    can_internal_costing = bool(getattr(access, "can_view_internal_costing", False))
    can_accounting = bool(getattr(access, "can_accounting_ca", False) or getattr(access, "can_accounting_bd", False))
    can_production = bool(getattr(access, "can_production", False))
    can_shipping = bool(getattr(access, "can_shipping", False))
    can_inventory = bool(getattr(access, "can_inventory", False))
    can_costing = bool(getattr(access, "can_costing", False))
    can_ai = bool(getattr(access, "can_ai", False))

    flags["can_view_page"] = bool(can_ai or can_ceo)
    flags["can_view_ceo_tools"] = can_ceo
    flags["can_view_financials"] = bool(can_accounting or can_ceo)
    flags["can_view_profit"] = bool(can_internal_costing)
    flags["can_view_quotations"] = bool(can_ceo or (can_costing and can_internal_costing))
    flags["can_view_production"] = bool(can_ceo or can_production)
    flags["can_view_shipping"] = bool(can_ceo or can_shipping or can_production)
    flags["can_view_inventory"] = bool(can_ceo or can_inventory)
    flags["can_view_lifecycle"] = bool(can_ceo or can_accounting or can_production or can_shipping or can_internal_costing)
    return flags


def _invoice_metrics(flags, today):
    if not flags["can_view_financials"]:
        return {
            "revenue_label": "Restricted",
            "outstanding_label": "Restricted",
            "overdue_count": 0,
            "overdue_rows": [],
        }
    month_start = today.replace(day=1)
    try:
        invoice_qs = Invoice.objects.exclude(status="cancelled")
        month_revenue = _decimal(invoice_qs.filter(issue_date__gte=month_start).aggregate(total=Sum("total_amount")).get("total"))
        open_qs = invoice_qs.filter(total_amount__gt=F("paid_amount")).exclude(status="paid")
        outstanding = _decimal(open_qs.aggregate(total=Sum(ExpressionWrapper(F("total_amount") - F("paid_amount"), output_field=DecimalField(max_digits=14, decimal_places=2)))).get("total"))
        overdue_qs = open_qs.filter(due_date__lt=today).select_related("customer").order_by("due_date", "-total_amount")
        rows = [
            {
                "label": _invoice_label(invoice),
                "metric": _money(invoice.balance, invoice.currency or "CAD"),
                "detail": f"Due {invoice.due_date:%b %d, %Y}" if invoice.due_date else "No due date",
                "url": _safe_reverse("invoice_view", invoice.pk),
                "tone": "bad",
            }
            for invoice in overdue_qs[:8]
        ]
        return {
            "revenue_label": _money(month_revenue),
            "outstanding_label": _money(outstanding),
            "overdue_count": overdue_qs.count(),
            "overdue_rows": rows,
        }
    except (OperationalError, ProgrammingError):
        return {
            "revenue_label": "Unavailable",
            "outstanding_label": "Unavailable",
            "overdue_count": 0,
            "overdue_rows": [],
        }


def _quotation_rows(flags, today, limit=8):
    if not flags["can_view_quotations"]:
        return []
    stale_date = today - timedelta(days=3)
    try:
        qs = (
            CostingHeader.objects.select_related("customer", "opportunity")
            .exclude(quotation_number="")
            .filter(Q(quoted_at__date__lte=stale_date) | Q(updated_at__date__lte=stale_date))
            .filter(invoices__isnull=True)
            .distinct()
            .order_by("quoted_at", "updated_at")[:limit]
        )
        return [
            {
                "label": _costing_label(costing),
                "metric": "Waiting approval",
                "detail": _customer_label(costing.customer),
                "url": _safe_reverse("cost_sheet_detail", costing.pk),
                "tone": "warn",
            }
            for costing in qs
        ]
    except (OperationalError, ProgrammingError):
        return []


def _production_metrics(flags, today):
    if not flags["can_view_production"]:
        return {"delayed_count": 0, "attention_rows": [], "qc_delayed_count": 0}
    try:
        delayed_qs = (
            ProductionOrder.objects.select_related("customer")
            .exclude(status__in=DONE_PRODUCTION_STATUSES)
            .filter(bulk_deadline__lt=today)
            .order_by("bulk_deadline", "-updated_at")
        )
        rows = [
            {
                "label": _production_label(order),
                "metric": "Delayed",
                "detail": f"Bulk deadline {order.bulk_deadline:%b %d, %Y}" if order.bulk_deadline else "No deadline",
                "url": _safe_reverse("production_detail", order.pk),
                "tone": "bad",
            }
            for order in delayed_qs[:8]
        ]
        hold_rows = [
            {
                "label": _production_label(order),
                "metric": "On hold",
                "detail": _customer_label(order.customer),
                "url": _safe_reverse("production_detail", order.pk),
                "tone": "warn",
            }
            for order in ProductionOrder.objects.select_related("customer").filter(status="hold").order_by("-updated_at")[:4]
        ]
        qc_delayed_count = ProductionStage.objects.filter(stage_key="qc", planned_end__lt=today).exclude(status="done").count()
        return {"delayed_count": delayed_qs.count(), "attention_rows": rows + hold_rows, "qc_delayed_count": qc_delayed_count}
    except (OperationalError, ProgrammingError):
        return {"delayed_count": 0, "attention_rows": [], "qc_delayed_count": 0}


def _shipment_metrics(flags, today):
    if not flags["can_view_shipping"]:
        return {"issue_count": 0, "issue_rows": []}
    try:
        issue_qs = (
            Shipment.objects.select_related("order", "customer")
            .exclude(status__in=["delivered", "cancelled"])
            .filter(Q(ship_date__lt=today) | Q(tracking_number=""))
            .order_by("ship_date", "-updated_at")
        )
        rows = [
            {
                "label": str(shipment),
                "metric": shipment.get_status_display(),
                "detail": "Missing tracking" if not shipment.tracking_number else f"Ship date {shipment.ship_date:%b %d, %Y}" if shipment.ship_date else "Needs update",
                "url": _safe_reverse("shipment_detail", shipment.pk),
                "tone": "warn",
            }
            for shipment in issue_qs[:8]
        ]
        return {"issue_count": issue_qs.count(), "issue_rows": rows}
    except (OperationalError, ProgrammingError):
        return {"issue_count": 0, "issue_rows": []}


def _inventory_metrics(flags, limit=8):
    if not flags["can_view_inventory"]:
        return {"low_count": 0, "critical_count": 0, "rows": []}
    try:
        active = InventoryItem.objects.filter(is_active=True)
        low_qs = active.filter(Q(quantity__lte=F("reorder_level")) | Q(reorder_level=0, quantity__lte=F("min_level"))).order_by("quantity", "name")
        critical_qs = active.filter(Q(quantity__lt=0) | Q(quantity__lte=F("minimum_stock")) | Q(minimum_stock=0, quantity__lte=F("min_level"))).order_by("quantity", "name")
        rows = [
            {
                "label": _inventory_label(item),
                "metric": f"{item.quantity} {item.unit_type}",
                "detail": f"Reorder level {item.effective_reorder_level}",
                "url": _safe_reverse("inventory_detail", item.pk),
                "tone": "bad" if item.quantity <= item.effective_minimum_stock else "warn",
            }
            for item in low_qs[:limit]
        ]
        return {"low_count": low_qs.count(), "critical_count": critical_qs.count(), "rows": rows}
    except (OperationalError, ProgrammingError):
        return {"low_count": 0, "critical_count": 0, "rows": []}


def _automation_alerts(flags, limit=8):
    visible = set()
    if flags["can_view_financials"]:
        visible.add("invoice")
    if flags["can_view_production"]:
        visible.add("production")
    if flags["can_view_inventory"]:
        visible.add("inventory")
    if flags["can_view_lifecycle"]:
        visible.add("lifecycle")
    if not visible:
        return []
    try:
        return list(
            AutomationNotification.objects.filter(is_resolved=False, rule_type__in=visible)
            .select_related("rule")
            .order_by("is_read", "-updated_at")[:limit]
        )
    except (OperationalError, ProgrammingError):
        return []


def _profit_rows(flags, limit=5):
    if not flags["can_view_profit"]:
        return []
    try:
        profit_expr = ExpressionWrapper(
            F("total_amount") - F("sewing_charge") - F("other_internal_cost"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        rows = (
            Invoice.objects.exclude(status="cancelled")
            .values("customer__account_brand", "customer__contact_name")
            .annotate(profit=Sum(profit_expr), revenue=Sum("total_amount"), invoice_count=Count("id"))
            .order_by("-profit")[:limit]
        )
        return [
            {
                "label": row.get("customer__account_brand") or row.get("customer__contact_name") or "No customer",
                "metric": _money(row.get("profit")),
                "detail": f"{row.get('invoice_count') or 0} invoice(s), revenue {_money(row.get('revenue'))}",
                "url": "",
                "tone": "good",
            }
            for row in rows
        ]
    except (OperationalError, ProgrammingError):
        return []


def _most_profitable_invoice(flags):
    if not flags["can_view_profit"]:
        return None
    try:
        profit_expr = ExpressionWrapper(
            F("total_amount") - F("sewing_charge") - F("other_internal_cost"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        invoice = (
            Invoice.objects.exclude(status="cancelled")
            .select_related("customer")
            .annotate(ai_profit=profit_expr)
            .order_by("-ai_profit", "-total_amount")
            .first()
        )
        if not invoice:
            return None
        return {
            "label": invoice.invoice_number,
            "metric": _money(getattr(invoice, "ai_profit", 0), invoice.currency or "CAD"),
            "detail": _customer_label(invoice.customer),
            "url": _safe_reverse("invoice_view", invoice.pk),
            "tone": "good",
        }
    except (OperationalError, ProgrammingError):
        return None


def _top_customer_this_month(flags, today):
    if not flags["can_view_financials"]:
        return None
    try:
        month_start = today.replace(day=1)
        row = (
            Invoice.objects.exclude(status="cancelled")
            .filter(issue_date__gte=month_start)
            .values("customer__account_brand", "customer__contact_name")
            .annotate(revenue=Sum("total_amount"), invoice_count=Count("id"))
            .order_by("-revenue")
            .first()
        )
        if not row:
            return None
        return {
            "label": row.get("customer__account_brand") or row.get("customer__contact_name") or "No customer",
            "metric": _money(row.get("revenue")),
            "detail": f"{row.get('invoice_count') or 0} invoice(s) this month",
            "url": "",
            "tone": "blue",
        }
    except (OperationalError, ProgrammingError):
        return None


def _recommendations(flags, invoice_metrics, quotation_rows, production_metrics, shipment_metrics, inventory_metrics, limit=12):
    recommendations = []

    for row in invoice_metrics.get("overdue_rows", [])[:4]:
        recommendations.append({"title": f"Contact customer for overdue payment: {row['label']}", "detail": row["detail"], "priority": 100, "tone": "bad", "url": row["url"]})
    for row in inventory_metrics.get("rows", [])[:4]:
        recommendations.append({"title": f"Reorder {row['label']}", "detail": row["detail"], "priority": 92 if row["tone"] == "bad" else 82, "tone": row["tone"], "url": row["url"]})
    for row in production_metrics.get("attention_rows", [])[:4]:
        recommendations.append({"title": f"Review production order: {row['label']}", "detail": row["detail"], "priority": 88 if row["tone"] == "bad" else 72, "tone": row["tone"], "url": row["url"]})
    for row in shipment_metrics.get("issue_rows", [])[:3]:
        recommendations.append({"title": f"Update shipment: {row['label']}", "detail": row["detail"], "priority": 78, "tone": "warn", "url": row["url"]})
    for row in quotation_rows[:3]:
        recommendations.append({"title": f"Follow up quotation {row['label']}", "detail": row["detail"], "priority": 70, "tone": "warn", "url": row["url"]})

    if flags["can_view_profit"]:
        try:
            low_margin = (
                Invoice.objects.exclude(status="cancelled")
                .filter(total_amount__gt=0)
                .select_related("customer")
                .annotate(
                    ai_profit=ExpressionWrapper(
                        F("total_amount") - F("sewing_charge") - F("other_internal_cost"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                )
                .annotate(
                    ai_margin_floor=ExpressionWrapper(
                        F("total_amount") * Decimal("0.10"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                )
                .filter(ai_profit__lt=F("ai_margin_floor"))
                .order_by("ai_profit", "-total_amount")
                .first()
            )
            if low_margin:
                recommendations.append(
                    {
                        "title": f"Review low margin order {low_margin.invoice_number}",
                        "detail": f"Estimated gross profit {_money(getattr(low_margin, 'ai_profit', 0), low_margin.currency or 'CAD')}.",
                        "priority": 76,
                        "tone": "warn",
                        "url": _safe_reverse("invoice_view", low_margin.pk),
                    }
                )
        except (OperationalError, ProgrammingError):
            pass

    seen = set()
    unique = []
    for item in sorted(recommendations, key=lambda row: row["priority"], reverse=True):
        key = (item["title"], item.get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:limit]


def _risk_score(invoice_metrics, production_metrics, shipment_metrics, inventory_metrics, quotation_rows):
    raw = (
        int(invoice_metrics.get("overdue_count") or 0) * 14
        + int(production_metrics.get("delayed_count") or 0) * 12
        + int(production_metrics.get("qc_delayed_count") or 0) * 8
        + int(shipment_metrics.get("issue_count") or 0) * 8
        + int(inventory_metrics.get("critical_count") or 0) * 10
        + len(quotation_rows) * 5
    )
    return min(100, raw)


def _question_answer(question, flags, invoice_metrics, quotation_rows, production_metrics, shipment_metrics, inventory_metrics):
    question_clean = (question or "").strip()
    normalized = question_clean.lower()
    if not question_clean:
        return {
            "question": "",
            "title": "Ask an operational question",
            "answer": "Choose a suggested question or type one. Answers are rule-based and use CRM data only.",
            "rows": [],
            "restricted": False,
        }

    if "profitable" in normalized or "profit" in normalized:
        if not flags["can_view_profit"]:
            return {
                "question": question_clean,
                "title": "Profit answer restricted",
                "answer": "Profit, margin, and internal cost answers require internal costing permission.",
                "rows": [],
                "restricted": True,
            }
        rows = _profit_rows(flags)
        return {
            "question": question_clean,
            "title": "Most profitable customers",
            "answer": "Ranked by invoice total minus sewing and other internal costs.",
            "rows": rows,
            "restricted": False,
        }

    if "quotation" in normalized or "quote" in normalized:
        return {
            "question": question_clean,
            "title": "Quotations needing follow-up",
            "answer": "Quotations with no invoice conversion and older activity are listed first.",
            "rows": quotation_rows,
            "restricted": not flags["can_view_quotations"],
        }

    if "material" in normalized or "reorder" in normalized or "stock" in normalized:
        return {
            "question": question_clean,
            "title": "Materials to reorder",
            "answer": "Materials at or below reorder thresholds are prioritized.",
            "rows": inventory_metrics.get("rows", []),
            "restricted": not flags["can_view_inventory"],
        }

    if "invoice" in normalized or "overdue" in normalized or "payment" in normalized:
        return {
            "question": question_clean,
            "title": "Overdue invoices",
            "answer": "Invoices past due with open balances are listed first.",
            "rows": invoice_metrics.get("overdue_rows", []),
            "restricted": not flags["can_view_financials"],
        }

    if "shipment" in normalized or "tracking" in normalized:
        return {
            "question": question_clean,
            "title": "Shipment issues",
            "answer": "Shipments with missing tracking or past ship dates are listed first.",
            "rows": shipment_metrics.get("issue_rows", []),
            "restricted": not flags["can_view_shipping"],
        }

    if "production" in normalized or "delayed" in normalized or "orders" in normalized:
        return {
            "question": question_clean,
            "title": "Production orders needing attention",
            "answer": "Delayed and held production orders are listed first.",
            "rows": production_metrics.get("attention_rows", []),
            "restricted": not flags["can_view_production"],
        }

    return {
        "question": question_clean,
        "title": "Operational answer",
        "answer": "I can answer questions about profitable customers, quotation follow-up, delayed orders, reorder materials, overdue invoices, and production attention.",
        "rows": [],
        "restricted": False,
    }


def build_ai_operations_context(user, question=""):
    today = timezone.localdate()
    flags = _access_flags(user)
    invoice_metrics = _invoice_metrics(flags, today)
    quotation_rows = _quotation_rows(flags, today)
    production_metrics = _production_metrics(flags, today)
    shipment_metrics = _shipment_metrics(flags, today)
    inventory_metrics = _inventory_metrics(flags)
    automation_alerts = _automation_alerts(flags)
    recommendations = _recommendations(flags, invoice_metrics, quotation_rows, production_metrics, shipment_metrics, inventory_metrics)
    risk_score = _risk_score(invoice_metrics, production_metrics, shipment_metrics, inventory_metrics, quotation_rows)

    daily_summary = [
        {"label": "Revenue", "value": invoice_metrics["revenue_label"], "note": "Invoiced this month.", "tone": "blue"},
        {"label": "Outstanding Invoices", "value": invoice_metrics["outstanding_label"], "note": f"{invoice_metrics['overdue_count']} overdue.", "tone": _tone_for_count(invoice_metrics["overdue_count"], 1, 3)},
        {"label": "Delayed Production", "value": production_metrics["delayed_count"] if flags["can_view_production"] else "Restricted", "note": "Orders past deadline.", "tone": _tone_for_count(production_metrics["delayed_count"], 1, 3)},
        {"label": "Shipment Issues", "value": shipment_metrics["issue_count"] if flags["can_view_shipping"] else "Restricted", "note": "Missing tracking or delayed.", "tone": _tone_for_count(shipment_metrics["issue_count"], 1, 3)},
        {"label": "Low Inventory", "value": inventory_metrics["low_count"] if flags["can_view_inventory"] else "Restricted", "note": f"{inventory_metrics['critical_count']} critical.", "tone": _tone_for_count(inventory_metrics["critical_count"], 1, 3)},
        {"label": "Critical Alerts", "value": len([a for a in automation_alerts if a.priority == "critical"]), "note": "Open automation alerts.", "tone": _tone_for_count(len([a for a in automation_alerts if a.priority == "critical"]), 1, 2)},
    ]

    risk_cards = []
    for row in invoice_metrics.get("overdue_rows", [])[:3]:
        risk_cards.append({"title": "Overdue invoice", **row})
    for row in production_metrics.get("attention_rows", [])[:3]:
        risk_cards.append({"title": "Production risk", **row})
    for row in inventory_metrics.get("rows", [])[:3]:
        risk_cards.append({"title": "Inventory shortage", **row})
    for row in shipment_metrics.get("issue_rows", [])[:2]:
        risk_cards.append({"title": "Shipment risk", **row})
    for row in quotation_rows[:2]:
        risk_cards.append({"title": "Quotation follow-up", **row})

    insights = []
    top_customer = _top_customer_this_month(flags, today)
    if top_customer:
        insights.append({"title": "Top customer this month", **top_customer})
    profitable_invoice = _most_profitable_invoice(flags)
    if profitable_invoice:
        insights.append({"title": "Most profitable order", **profitable_invoice})
    if risk_cards:
        highest = risk_cards[0]
        insights.append({"title": "Highest risk order", "label": highest["label"], "metric": highest["metric"], "detail": highest["detail"], "url": highest["url"], "tone": highest["tone"]})
    if inventory_metrics.get("rows"):
        row = inventory_metrics["rows"][0]
        insights.append({"title": "Inventory concern", **row})
    if production_metrics.get("qc_delayed_count"):
        insights.append({"title": "Production bottleneck", "label": "QC delayed", "metric": production_metrics["qc_delayed_count"], "detail": "QC stages past planned end.", "url": "", "tone": "warn"})

    question_answer = _question_answer(question, flags, invoice_metrics, quotation_rows, production_metrics, shipment_metrics, inventory_metrics)

    return {
        "today": today,
        "access_flags": flags,
        "daily_summary": daily_summary,
        "recommendations": recommendations,
        "risk_score": risk_score,
        "risk_tone": "bad" if risk_score >= 70 else "warn" if risk_score >= 30 else "good",
        "risk_cards": risk_cards[:10],
        "question_answer": question_answer,
        "automation_alerts": automation_alerts,
        "executive_insights": insights[:6],
        "suggested_questions": [
            "Which customers are most profitable?",
            "Which quotations need follow up?",
            "Which orders are delayed?",
            "Which materials should be reordered?",
            "Which invoices are overdue?",
            "Which production orders need attention?",
        ],
        "operations_counts": {
            "quotation_followups": len(quotation_rows),
            "overdue_invoices": invoice_metrics.get("overdue_count") or 0,
            "delayed_production": production_metrics.get("delayed_count") or 0,
            "shipment_issues": shipment_metrics.get("issue_count") or 0,
            "low_inventory": inventory_metrics.get("low_count") or 0,
            "critical_inventory": inventory_metrics.get("critical_count") or 0,
        },
    }
