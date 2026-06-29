from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import models
from django.db.models import F, Q
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from crm.models import (
    AutomationNotification,
    AutomationRule,
    AutomationTask,
    InventoryItem,
    OrderLifecycle,
    ProductionOrder,
    ProductionStage,
)
from crm.permissions import get_access

try:
    from crm.models import Invoice
except Exception:  # pragma: no cover - defensive for old imports
    Invoice = None


DEFAULT_AUTOMATION_RULES = [
    {
        "rule_name": "Invoice Due Soon",
        "rule_type": "invoice",
        "trigger": "daily_dashboard_sync",
        "condition": {"due_in_days": 3, "balance": "open"},
        "action": {"dashboard_alert": True, "notification": True, "task": True, "send_email": False},
    },
    {
        "rule_name": "Invoice Overdue",
        "rule_type": "invoice",
        "trigger": "daily_dashboard_sync",
        "condition": {"due_date": "past", "balance": "open"},
        "action": {"dashboard_alert": True, "notification": True, "task": True, "send_email": False},
    },
    {
        "rule_name": "Partial Payment Stalled",
        "rule_type": "invoice",
        "trigger": "daily_dashboard_sync",
        "condition": {"partial_payment_age_days": 7},
        "action": {"dashboard_alert": True, "notification": True, "task": True, "send_email": False},
    },
    {
        "rule_name": "Production Delayed",
        "rule_type": "production",
        "trigger": "daily_dashboard_sync",
        "condition": {"bulk_deadline": "past", "status": "active"},
        "action": {"dashboard_alert": True, "production_alert": True, "task": True},
    },
    {
        "rule_name": "QC Delayed",
        "rule_type": "production",
        "trigger": "daily_dashboard_sync",
        "condition": {"stage": "qc", "planned_end": "past", "status": "not_done"},
        "action": {"dashboard_alert": True, "production_alert": True, "task": True},
    },
    {
        "rule_name": "Shipment Pending",
        "rule_type": "production",
        "trigger": "daily_dashboard_sync",
        "condition": {"production_status": "complete", "shipment": "not_shipped"},
        "action": {"dashboard_alert": True, "production_alert": True, "task": True},
    },
    {
        "rule_name": "Low Stock",
        "rule_type": "inventory",
        "trigger": "daily_dashboard_sync",
        "condition": {"quantity": "at_or_below_reorder_level"},
        "action": {"dashboard_alert": True, "inventory_alert": True, "task": True},
    },
    {
        "rule_name": "Critical Stock",
        "rule_type": "inventory",
        "trigger": "daily_dashboard_sync",
        "condition": {"quantity": "at_or_below_minimum_or_negative"},
        "action": {"dashboard_alert": True, "inventory_alert": True, "task": True},
    },
    {
        "rule_name": "High Waste",
        "rule_type": "inventory",
        "trigger": "daily_dashboard_sync",
        "condition": {"waste_or_damaged_quantity": "positive"},
        "action": {"dashboard_alert": True, "inventory_alert": True, "task": True},
    },
    {
        "rule_name": "Quotation Waiting Approval",
        "rule_type": "lifecycle",
        "trigger": "daily_dashboard_sync",
        "condition": {"quotation": "draft_or_waiting", "age_days": 3},
        "action": {"dashboard_alert": True, "notification": True, "task": True},
    },
    {
        "rule_name": "Invoice Waiting Payment",
        "rule_type": "lifecycle",
        "trigger": "daily_dashboard_sync",
        "condition": {"invoice_balance": "open"},
        "action": {"dashboard_alert": True, "notification": True, "task": True},
    },
    {
        "rule_name": "Production Waiting Update",
        "rule_type": "lifecycle",
        "trigger": "daily_dashboard_sync",
        "condition": {"production_status": "active", "updated_age_days": 7},
        "action": {"dashboard_alert": True, "notification": True, "task": True},
    },
    {
        "rule_name": "Shipment Waiting Tracking",
        "rule_type": "lifecycle",
        "trigger": "daily_dashboard_sync",
        "condition": {"shipment_tracking": "missing"},
        "action": {"dashboard_alert": True, "notification": True, "task": True},
    },
]

AUTOMATION_DASHBOARD_SYNC_TIMEOUT = 300


def _safe_reverse(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


def _record_content(record):
    if not record:
        return None, None
    return ContentType.objects.get_for_model(record, for_concrete_model=False), record.pk


def _customer_name(customer):
    if not customer:
        return ""
    return getattr(customer, "account_brand", "") or getattr(customer, "contact_name", "") or str(customer)


def _invoice_label(invoice):
    customer = _customer_name(getattr(invoice, "customer", None))
    return f"{invoice.invoice_number}{' - ' + customer if customer else ''}"


def _production_label(order):
    return getattr(order, "order_code", "") or getattr(order, "title", "") or f"Production {order.pk}"


def _inventory_label(item):
    return getattr(item, "name", "") or f"Inventory {item.pk}"


def _lifecycle_label(lifecycle):
    invoice = getattr(lifecycle, "invoice", None)
    if invoice:
        return _invoice_label(invoice)
    order = getattr(lifecycle, "production_order", None)
    if order:
        return _production_label(order)
    quote = getattr(lifecycle, "quotation", None)
    if quote:
        return getattr(quote, "quotation_number", "") or f"Costing {quote.pk}"
    return f"Lifecycle {lifecycle.pk}"


def _ensure_default_rules(created_by=None):
    rules = {}
    for spec in DEFAULT_AUTOMATION_RULES:
        rule, created = AutomationRule.objects.get_or_create(
            rule_name=spec["rule_name"],
            defaults={
                "rule_type": spec["rule_type"],
                "trigger": spec["trigger"],
                "condition": spec["condition"],
                "action": spec["action"],
                "created_by": created_by,
            },
        )
        if not created:
            changed = False
            for field in ("rule_type", "trigger", "condition", "action"):
                if getattr(rule, field) in ("", {}, None):
                    setattr(rule, field, spec[field])
                    changed = True
            if changed:
                rule.save(update_fields=["rule_type", "trigger", "condition", "action", "updated_at"])
        rules[rule.rule_name] = rule
    return rules


def _upsert_notification(
    *,
    rule,
    source_key,
    title,
    message,
    priority,
    record=None,
    record_label="",
    target_url="",
    task_title="",
    task_description="",
    task_due_date=None,
    task_priority="normal",
    created_by=None,
):
    content_type, object_id = _record_content(record)
    AutomationNotification.objects.filter(
        source_key=source_key,
        is_resolved=False,
    ).update(
        is_resolved=True,
        resolved_at=timezone.now(),
    )
    notification = None
    if task_title:
        task_content_type, task_object_id = _record_content(record)
        task, _task_created = AutomationTask.objects.get_or_create(
            source_key=f"task:{source_key}",
            defaults={
                "rule": rule,
                "notification": notification,
                "title": task_title,
                "description": task_description,
                "priority": task_priority,
                "due_date": task_due_date,
                "record_content_type": task_content_type,
                "record_object_id": task_object_id,
                "record_label": record_label,
                "target_url": target_url,
                "created_by": created_by,
            },
        )
        if task.status not in {"done", "cancelled"}:
            task.notification = None
            task.title = task_title
            task.description = task_description
            task.priority = task_priority
            task.due_date = task_due_date
            task.record_content_type = task_content_type
            task.record_object_id = task_object_id
            task.record_label = record_label
            task.target_url = target_url
            task.save(
                update_fields=[
                    "notification",
                    "title",
                    "description",
                    "priority",
                    "due_date",
                    "record_content_type",
                    "record_object_id",
                    "record_label",
                    "target_url",
                    "updated_at",
                ]
            )
    return notification


def _enabled(rule):
    return bool(rule and rule.enabled)


def _sync_invoice_rules(rules, today, created_by=None):
    active_keys = set()
    if Invoice is None:
        return active_keys

    due_soon_rule = rules.get("Invoice Due Soon")
    overdue_rule = rules.get("Invoice Overdue")
    stalled_rule = rules.get("Partial Payment Stalled")

    if _enabled(due_soon_rule):
        qs = (
            Invoice.objects.select_related("customer")
            .exclude(status__in=["paid", "cancelled"])
            .filter(total_amount__gt=F("paid_amount"), due_date__range=(today, today + timedelta(days=3)))
            .order_by("due_date", "-total_amount")[:40]
        )
        for invoice in qs:
            key = f"crm-auto:invoice_due_soon:{invoice.pk}"
            active_keys.add(key)
            label = _invoice_label(invoice)
            _upsert_notification(
                rule=due_soon_rule,
                source_key=key,
                title="Invoice due soon",
                message=f"{invoice.invoice_number} is due on {invoice.due_date}.",
                priority="high",
                record=invoice,
                record_label=label,
                target_url=_safe_reverse("invoice_view", invoice.pk),
                task_title="Call customer about invoice due soon",
                task_description=f"Follow up before {invoice.invoice_number} reaches due date.",
                task_due_date=invoice.due_date,
                task_priority="high",
                created_by=created_by,
            )

    if _enabled(overdue_rule):
        qs = (
            Invoice.objects.select_related("customer")
            .exclude(status__in=["paid", "cancelled"])
            .filter(total_amount__gt=F("paid_amount"), due_date__lt=today)
            .order_by("due_date", "-total_amount")[:60]
        )
        for invoice in qs:
            key = f"crm-auto:invoice_overdue:{invoice.pk}"
            active_keys.add(key)
            label = _invoice_label(invoice)
            _upsert_notification(
                rule=overdue_rule,
                source_key=key,
                title="Invoice overdue",
                message=f"{invoice.invoice_number} is overdue and still has an open balance.",
                priority="critical",
                record=invoice,
                record_label=label,
                target_url=_safe_reverse("invoice_view", invoice.pk),
                task_title="Call customer about overdue invoice",
                task_description=f"Collect or update payment status for {invoice.invoice_number}.",
                task_due_date=today,
                task_priority="urgent",
                created_by=created_by,
            )

    if _enabled(stalled_rule):
        stalled_before = today - timedelta(days=7)
        qs = (
            Invoice.objects.select_related("customer")
            .exclude(status__in=["paid", "cancelled"])
            .filter(paid_amount__gt=0, total_amount__gt=F("paid_amount"), updated_at__date__lte=stalled_before)
            .order_by("updated_at", "due_date")[:40]
        )
        for invoice in qs:
            key = f"crm-auto:partial_payment_stalled:{invoice.pk}"
            active_keys.add(key)
            label = _invoice_label(invoice)
            _upsert_notification(
                rule=stalled_rule,
                source_key=key,
                title="Partial payment stalled",
                message=f"{invoice.invoice_number} has a partial payment with no recent update.",
                priority="high",
                record=invoice,
                record_label=label,
                target_url=_safe_reverse("invoice_view", invoice.pk),
                task_title="Follow up stalled partial payment",
                task_description=f"Confirm next payment date for {invoice.invoice_number}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )
    return active_keys


def _sync_production_rules(rules, today, created_by=None):
    active_keys = set()
    completed = ["done", "closed_won", "closed_lost"]

    delayed_rule = rules.get("Production Delayed")
    if _enabled(delayed_rule):
        qs = (
            ProductionOrder.objects.select_related("customer")
            .exclude(status__in=completed)
            .filter(bulk_deadline__lt=today)
            .order_by("bulk_deadline", "-updated_at")[:60]
        )
        for order in qs:
            key = f"crm-auto:production_delayed:{order.pk}"
            active_keys.add(key)
            label = _production_label(order)
            _upsert_notification(
                rule=delayed_rule,
                source_key=key,
                title="Production delayed",
                message=f"{label} is past the bulk deadline.",
                priority="critical",
                record=order,
                record_label=label,
                target_url=_safe_reverse("production_detail", order.pk),
                task_title="Review delayed production order",
                task_description=f"Check blockers and update timeline for {label}.",
                task_due_date=today,
                task_priority="urgent",
                created_by=created_by,
            )

    qc_rule = rules.get("QC Delayed")
    if _enabled(qc_rule):
        qs = (
            ProductionStage.objects.select_related("order", "order__customer")
            .filter(stage_key="qc", planned_end__lt=today)
            .exclude(status="done")
            .order_by("planned_end", "-updated_at")[:40]
        )
        for stage in qs:
            order = stage.order
            key = f"crm-auto:qc_delayed:{stage.pk}"
            active_keys.add(key)
            label = f"{_production_label(order)} - QC"
            _upsert_notification(
                rule=qc_rule,
                source_key=key,
                title="QC delayed",
                message=f"QC is past planned end for {_production_label(order)}.",
                priority="high",
                record=stage,
                record_label=label,
                target_url=_safe_reverse("production_detail", order.pk),
                task_title="Review delayed QC stage",
                task_description=f"Update QC status and notes for {_production_label(order)}.",
                task_due_date=today,
                task_priority="high",
                created_by=created_by,
            )

    shipment_rule = rules.get("Shipment Pending")
    if _enabled(shipment_rule):
        qs = (
            ProductionOrder.objects.select_related("customer")
            .filter(status__in=["done", "closed_won"])
            .exclude(shipments__status__in=["shipped", "out_for_delivery", "delivered"])
            .distinct()
            .order_by("-updated_at")[:50]
        )
        for order in qs:
            key = f"crm-auto:shipment_pending:{order.pk}"
            active_keys.add(key)
            label = _production_label(order)
            _upsert_notification(
                rule=shipment_rule,
                source_key=key,
                title="Shipment pending",
                message=f"{label} is complete but has no shipped shipment record.",
                priority="high",
                record=order,
                record_label=label,
                target_url=_safe_reverse("production_detail", order.pk),
                task_title="Create shipment for completed order",
                task_description=f"Create or update shipment for {label}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )
    return active_keys


def _sync_inventory_rules(rules, today, created_by=None):
    active_keys = set()
    low_rule = rules.get("Low Stock")
    critical_rule = rules.get("Critical Stock")
    waste_rule = rules.get("High Waste")

    if _enabled(low_rule):
        qs = (
            InventoryItem.objects.filter(is_active=True)
            .filter(Q(quantity__lte=F("reorder_level")) | Q(reorder_level=0, quantity__lte=F("min_level")))
            .order_by("quantity", "name")[:60]
        )
        for item in qs:
            key = f"crm-auto:low_stock:{item.pk}"
            active_keys.add(key)
            label = _inventory_label(item)
            _upsert_notification(
                rule=low_rule,
                source_key=key,
                title="Low stock",
                message=f"{label} is at or below reorder level.",
                priority="high",
                record=item,
                record_label=label,
                target_url=_safe_reverse("inventory_detail", item.pk),
                task_title=f"Order {label}",
                task_description=f"Review reorder quantity for {label}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )

    if _enabled(critical_rule):
        qs = (
            InventoryItem.objects.filter(is_active=True)
            .filter(Q(quantity__lt=0) | Q(quantity__lte=F("minimum_stock")) | Q(minimum_stock=0, quantity__lte=F("min_level")))
            .order_by("quantity", "name")[:60]
        )
        for item in qs:
            key = f"crm-auto:critical_stock:{item.pk}"
            active_keys.add(key)
            label = _inventory_label(item)
            _upsert_notification(
                rule=critical_rule,
                source_key=key,
                title="Critical stock",
                message=f"{label} is at critical stock level.",
                priority="critical",
                record=item,
                record_label=label,
                target_url=_safe_reverse("inventory_detail", item.pk),
                task_title=f"Urgent stock review for {label}",
                task_description=f"Confirm availability and reorder plan for {label}.",
                task_due_date=today,
                task_priority="urgent",
                created_by=created_by,
            )

    if _enabled(waste_rule):
        qs = (
            InventoryItem.objects.filter(is_active=True)
            .filter(Q(waste_quantity__gt=0) | Q(damaged_quantity__gt=0))
            .order_by("-waste_quantity", "-damaged_quantity", "name")[:40]
        )
        for item in qs:
            waste_total = (item.waste_quantity or 0) + (item.damaged_quantity or 0)
            key = f"crm-auto:high_waste:{item.pk}"
            active_keys.add(key)
            label = _inventory_label(item)
            _upsert_notification(
                rule=waste_rule,
                source_key=key,
                title="High waste",
                message=f"{label} has recorded waste or damaged material ({waste_total}).",
                priority="high",
                record=item,
                record_label=label,
                target_url=_safe_reverse("inventory_detail", item.pk),
                task_title=f"Review waste for {label}",
                task_description=f"Check damaged/waste usage and update production notes for {label}.",
                task_due_date=today + timedelta(days=2),
                task_priority="high",
                created_by=created_by,
            )
    return active_keys


def _sync_lifecycle_rules(rules, today, created_by=None):
    active_keys = set()
    quote_rule = rules.get("Quotation Waiting Approval")
    payment_rule = rules.get("Invoice Waiting Payment")
    production_rule = rules.get("Production Waiting Update")
    tracking_rule = rules.get("Shipment Waiting Tracking")

    if _enabled(quote_rule):
        stale_at = timezone.now() - timedelta(days=3)
        lifecycle_qs = (
            OrderLifecycle.objects.select_related("quotation", "customer")
            .filter(status="quotation", invoice__isnull=True, updated_at__lte=stale_at)
            .order_by("updated_at")[:40]
        )
        for lifecycle in lifecycle_qs:
            key = f"crm-auto:quotation_waiting_approval:{lifecycle.pk}"
            active_keys.add(key)
            label = _lifecycle_label(lifecycle)
            _upsert_notification(
                rule=quote_rule,
                source_key=key,
                title="Quotation waiting approval",
                message=f"{label} has been in quotation stage for more than 3 days.",
                priority="high",
                record=lifecycle,
                record_label=label,
                target_url=_safe_reverse("order_lifecycle_detail", lifecycle.pk),
                task_title="Follow up quotation",
                task_description=f"Review quotation approval status for {label}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )

    if _enabled(payment_rule):
        lifecycle_qs = (
            OrderLifecycle.objects.select_related("invoice", "customer")
            .filter(invoice__isnull=False, invoice__total_amount__gt=F("invoice__paid_amount"))
            .exclude(status__in=["completed", "cancelled"])
            .order_by("invoice__due_date", "-updated_at")[:60]
        )
        for lifecycle in lifecycle_qs:
            key = f"crm-auto:lifecycle_invoice_waiting_payment:{lifecycle.pk}"
            active_keys.add(key)
            label = _lifecycle_label(lifecycle)
            _upsert_notification(
                rule=payment_rule,
                source_key=key,
                title="Invoice waiting payment",
                message=f"{label} has an open payment balance.",
                priority="high",
                record=lifecycle,
                record_label=label,
                target_url=_safe_reverse("order_lifecycle_detail", lifecycle.pk),
                task_title="Follow up invoice payment",
                task_description=f"Confirm payment plan for {label}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )

    if _enabled(production_rule):
        stale_at = timezone.now() - timedelta(days=7)
        lifecycle_qs = (
            OrderLifecycle.objects.select_related("production_order", "customer")
            .filter(status="production", production_order__updated_at__lte=stale_at)
            .exclude(production_order__status__in=["done", "closed_won", "closed_lost"])
            .order_by("production_order__updated_at")[:40]
        )
        for lifecycle in lifecycle_qs:
            key = f"crm-auto:production_waiting_update:{lifecycle.pk}"
            active_keys.add(key)
            label = _lifecycle_label(lifecycle)
            _upsert_notification(
                rule=production_rule,
                source_key=key,
                title="Production waiting update",
                message=f"{label} production has not been updated in 7 days.",
                priority="high",
                record=lifecycle,
                record_label=label,
                target_url=_safe_reverse("order_lifecycle_detail", lifecycle.pk),
                task_title="Update production status",
                task_description=f"Add latest production update for {label}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )

    if _enabled(tracking_rule):
        lifecycle_qs = (
            OrderLifecycle.objects.select_related("shipping_record", "production_order", "customer")
            .filter(status="shipping")
            .filter(Q(shipping_record__isnull=True) | Q(shipping_record__tracking_number=""))
            .order_by("-updated_at")[:40]
        )
        for lifecycle in lifecycle_qs:
            key = f"crm-auto:shipment_waiting_tracking:{lifecycle.pk}"
            active_keys.add(key)
            label = _lifecycle_label(lifecycle)
            _upsert_notification(
                rule=tracking_rule,
                source_key=key,
                title="Shipment waiting tracking",
                message=f"{label} is in shipping stage without tracking information.",
                priority="high",
                record=lifecycle,
                record_label=label,
                target_url=_safe_reverse("order_lifecycle_detail", lifecycle.pk),
                task_title="Add shipment tracking",
                task_description=f"Add courier and tracking number for {label}.",
                task_due_date=today + timedelta(days=1),
                task_priority="high",
                created_by=created_by,
            )
    return active_keys


def sync_automation_engine(created_by=None):
    today = timezone.localdate()
    try:
        rules = _ensure_default_rules(created_by=created_by)
        active_keys = set()
        active_keys |= _sync_invoice_rules(rules, today, created_by=created_by)
        active_keys |= _sync_production_rules(rules, today, created_by=created_by)
        active_keys |= _sync_inventory_rules(rules, today, created_by=created_by)
        active_keys |= _sync_lifecycle_rules(rules, today, created_by=created_by)
        now = timezone.now()
        AutomationNotification.objects.filter(
            source_key__startswith="crm-auto:",
            is_resolved=False,
        ).update(is_resolved=True, resolved_at=now)
        return {"created": len(active_keys), "error": ""}
    except (OperationalError, ProgrammingError) as exc:
        return {"created": 0, "error": str(exc)}


def _access_flags(user):
    flags = {
        "invoice": False,
        "production": False,
        "inventory": False,
        "lifecycle": False,
    }
    if not user or not getattr(user, "is_authenticated", False):
        return flags
    if getattr(user, "is_superuser", False):
        return {key: True for key in flags}
    try:
        access = get_access(user)
    except (OperationalError, ProgrammingError):
        return flags
    flags["invoice"] = bool(getattr(access, "can_accounting_ca", False) or getattr(access, "can_accounting_bd", False))
    flags["production"] = bool(getattr(access, "can_production", False))
    flags["inventory"] = bool(getattr(access, "can_inventory", False))
    flags["lifecycle"] = bool(
        getattr(access, "can_view_internal_costing", False)
        or flags["invoice"]
        or flags["production"]
        or getattr(access, "can_shipping", False)
    )
    return flags


def _visible_rule_types(user):
    flags = _access_flags(user)
    visible = set()
    for rule_type, allowed in flags.items():
        if allowed:
            visible.add(rule_type)
    return visible


def _priority_rank_expression():
    return models.Case(
        models.When(priority="critical", then=models.Value(4)),
        models.When(priority="high", then=models.Value(3)),
        models.When(priority="normal", then=models.Value(2)),
        default=models.Value(1),
        output_field=models.IntegerField(),
    )


def automation_dashboard_context(user, *, sync=True, limit=12):
    visible_rule_types = _visible_rule_types(user)
    empty_context = {
        "automation_notifications": [],
        "automation_notification_cards": [],
        "automation_tasks": [],
        "automation_unread_count": 0,
        "automation_task_count": 0,
        "executive_automation_cards": [],
        "orders_needing_attention": [],
        "overdue_payment_notifications": [],
        "critical_inventory_notifications": [],
    }
    if not visible_rule_types:
        return empty_context

    if sync:
        sync_key = f"crm:automation-dashboard-sync:{timezone.localdate().isoformat()}"
        if cache.add(sync_key, True, timeout=AUTOMATION_DASHBOARD_SYNC_TIMEOUT):
            try:
                result = sync_automation_engine(
                    created_by=user if getattr(user, "is_authenticated", False) else None
                )
            except Exception:
                cache.delete(sync_key)
                raise
            if result.get("error"):
                cache.delete(sync_key)

    try:
        notifications_qs = (
            AutomationNotification.objects.filter(is_resolved=False, rule_type__in=visible_rule_types)
            .select_related("rule", "record_content_type")
            .annotate(priority_rank=_priority_rank_expression())
            .order_by("is_read", "-priority_rank", "-updated_at")
        )
        notifications = list(notifications_qs[:limit])
        unread_count = notifications_qs.filter(is_read=False).count()

        summary_rows = list(
            notifications_qs.values("rule_type")
            .annotate(count=models.Count("id"))
            .order_by("-count")
        )
        notification_cards = [
            {
                "label": row["rule_type"].title(),
                "count": int(row["count"] or 0),
                "detail": "Open automation alert(s).",
                "tone": "warn" if row["count"] else "good",
                "href": "#notification-center",
            }
            for row in summary_rows
        ]
        if not notification_cards:
            notification_cards = [
                {
                    "label": "Automation",
                    "count": 0,
                    "detail": "No active automation alerts.",
                    "tone": "good",
                    "href": "#notification-center",
                }
            ]

        tasks_qs = (
            AutomationTask.objects.filter(status__in=["open", "in_progress"])
            .filter(rule__rule_type__in=visible_rule_types)
            .filter(Q(notification__isnull=True) | Q(notification__is_resolved=False))
            .select_related("rule", "notification", "record_content_type")
            .order_by("due_date", "-updated_at")
        )
        tasks = list(tasks_qs[:8])

        executive_qs = notifications_qs.filter(priority__in=["critical", "high"])
        orders_attention = list(executive_qs.filter(rule_type__in=["production", "lifecycle"])[:6])
        overdue_payments = list(executive_qs.filter(rule_type="invoice")[:6]) if "invoice" in visible_rule_types else []
        inventory_alerts = list(executive_qs.filter(rule_type="inventory")[:6]) if "inventory" in visible_rule_types else []

        executive_cards = [
            {
                "title": "Top Risks",
                "count": executive_qs.count(),
                "detail": "Critical and high-priority automation alerts.",
                "tone": "bad" if executive_qs.filter(priority="critical").exists() else "warn",
            },
            {
                "title": "Orders Needing Attention",
                "count": len(orders_attention),
                "detail": "Production and lifecycle alerts.",
                "tone": "warn" if orders_attention else "good",
            },
            {
                "title": "Overdue Payments",
                "count": len(overdue_payments),
                "detail": "Invoice collection alerts.",
                "tone": "bad" if overdue_payments else "good",
            },
            {
                "title": "Critical Inventory",
                "count": len(inventory_alerts),
                "detail": "Low, critical, or waste inventory alerts.",
                "tone": "bad" if inventory_alerts else "good",
            },
        ]

        return {
            "automation_notifications": notifications,
            "automation_notification_cards": notification_cards,
            "automation_tasks": tasks,
            "automation_unread_count": unread_count,
            "automation_task_count": tasks_qs.count(),
            "executive_automation_cards": executive_cards,
            "orders_needing_attention": orders_attention,
            "overdue_payment_notifications": overdue_payments,
            "critical_inventory_notifications": inventory_alerts,
        }
    except (OperationalError, ProgrammingError):
        return empty_context
