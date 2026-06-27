import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import F, Q
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from crm.models import AutomationNotification, Invoice, ProductionOrder, Shipment
from crm.services.operations_permissions import (
    ROLE_ACCOUNTS,
    ROLE_CEO,
    ROLE_PRODUCTION,
    can_access_operations_module,
    operations_role_names,
)


logger = logging.getLogger(__name__)


def _safe_reverse(name, pk):
    try:
        return reverse(name, args=[pk])
    except NoReverseMatch:
        return ""


def _recipient_rows(role_names):
    User = get_user_model()
    roles = tuple(dict.fromkeys(role_names))
    rows = list(
        User.objects.filter(is_active=True)
        .filter(Q(groups__name__in=roles) | Q(is_superuser=True))
        .distinct()
        .only("id", "is_superuser")
    )
    if rows:
        return [(user, roles[0] if roles else "") for user in rows]
    return [(None, role) for role in roles]


def create_operations_notification(
    *,
    source_key,
    notification_type,
    title,
    message,
    related_module,
    record=None,
    roles=(),
    priority="normal",
    due_date=None,
    target_url="",
    record_label="",
    recipient_rows=None,
):
    content_type = ContentType.objects.get_for_model(record, for_concrete_model=False) if record else None
    object_id = record.pk if record else None
    active_keys = set()
    for user, role in recipient_rows if recipient_rows is not None else _recipient_rows(roles):
        recipient_key = f"user:{user.pk}" if user else f"role:{role.lower()}"
        key = f"{source_key}:{recipient_key}"
        active_keys.add(key)
        AutomationNotification.objects.update_or_create(
            source_key=key,
            defaults={
                "rule": None,
                "rule_type": related_module,
                "notification_type": notification_type,
                "title": title,
                "message": message,
                "priority": priority,
                "is_resolved": False,
                "resolved_at": None,
                "record_content_type": content_type,
                "record_object_id": object_id,
                "record_label": record_label,
                "target_url": target_url,
                "assigned_user": user,
                "assigned_role": role,
                "due_date": due_date,
            },
        )
    return active_keys


def notify_quotation_waiting_approval(costing):
    label = costing.quotation_number or f"Quotation {costing.pk}"
    return create_operations_notification(
        source_key=f"operations:ceo_approval:{costing.pk}",
        notification_type="ceo_approval",
        title="Quotation awaiting CEO approval",
        message=f"{label} is ready for CEO review.",
        related_module="lifecycle",
        record=costing,
        roles=(ROLE_CEO,),
        priority="high",
        due_date=timezone.localdate(),
        target_url=_safe_reverse("cost_sheet_client_quotation", costing.pk),
        record_label=label,
    )


def notify_ready_to_ship(order):
    label = order.order_code or order.title or f"Production {order.pk}"
    return create_operations_notification(
        source_key=f"operations:ready_to_ship:{order.pk}",
        notification_type="shipping",
        title="Order ready to ship",
        message=f"{label} is ready for shipment planning.",
        related_module="production",
        record=order,
        roles=(ROLE_PRODUCTION, ROLE_CEO),
        priority="high",
        due_date=order.bulk_deadline,
        target_url=_safe_reverse("production_detail", order.pk),
        record_label=label,
    )


def sync_operations_notifications(today=None, *, force=False):
    today = today or timezone.localdate()
    cache_key = f"operations-notification-sync:{today.isoformat()}"
    if not force and cache.get(cache_key):
        return {"active": 0, "error": "", "cached": True}
    active_keys = set()
    try:
        production_recipients = _recipient_rows((ROLE_PRODUCTION, ROLE_CEO))
        accounts_recipients = _recipient_rows((ROLE_ACCOUNTS, ROLE_CEO))
        due_orders = (
            ProductionOrder.objects.filter(
                is_archived=False,
                bulk_deadline__range=(today, today + timedelta(days=7)),
            )
            .exclude(operational_status__in=["shipped", "cancelled"])
            .only("id", "order_code", "title", "bulk_deadline")[:80]
        )
        for order in due_orders:
            label = order.order_code or order.title or f"Production {order.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:production_due:{order.pk}",
                notification_type="production_due",
                title="Production delivery approaching",
                message=f"{label} is due on {order.bulk_deadline:%b %d, %Y}.",
                related_module="production",
                record=order,
                roles=(ROLE_PRODUCTION, ROLE_CEO),
                priority="urgent" if order.bulk_deadline <= today + timedelta(days=2) else "high",
                due_date=order.bulk_deadline,
                target_url=_safe_reverse("production_detail", order.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        ready_orders = ProductionOrder.objects.filter(
            is_archived=False,
            operational_status="ready_to_ship",
        ).only("id", "order_code", "title", "bulk_deadline")[:80]
        for order in ready_orders:
            label = order.order_code or order.title or f"Production {order.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:ready_to_ship:{order.pk}",
                notification_type="shipping",
                title="Order ready to ship",
                message=f"{label} is ready for shipment planning.",
                related_module="production",
                record=order,
                roles=(ROLE_PRODUCTION, ROLE_CEO),
                priority="high",
                due_date=order.bulk_deadline,
                target_url=_safe_reverse("production_detail", order.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        near_shipments = (
            Shipment.objects.select_related("order")
            .filter(ship_date__range=(today, today + timedelta(days=3)))
            .exclude(status__in=["shipped", "out_for_delivery", "delivered", "cancelled"])
            .only("id", "ship_date", "order__id", "order__order_code", "order__title")[:80]
        )
        for shipment in near_shipments:
            order = shipment.order
            label = (order.order_code or order.title) if order else f"Shipment {shipment.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:shipment_due:{shipment.pk}",
                notification_type="shipping",
                title="Shipment date approaching",
                message=f"{label} is scheduled to ship on {shipment.ship_date:%b %d, %Y}.",
                related_module="production",
                record=shipment,
                roles=(ROLE_PRODUCTION, ROLE_CEO),
                priority="high",
                due_date=shipment.ship_date,
                target_url=_safe_reverse("shipment_detail", shipment.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        overdue_invoices = (
            Invoice.objects.select_related("customer")
            .exclude(status__in=["paid", "cancelled"])
            .filter(due_date__lt=today, total_amount__gt=F("paid_amount"))
            .only("id", "invoice_number", "due_date", "customer__account_brand", "customer__contact_name")[:100]
        )
        for invoice in overdue_invoices:
            label = invoice.invoice_number
            active_keys |= create_operations_notification(
                source_key=f"operations:invoice_overdue:{invoice.pk}",
                notification_type="invoice_overdue",
                title="Invoice overdue",
                message=f"{label} has an outstanding balance and was due on {invoice.due_date:%b %d, %Y}.",
                related_module="invoice",
                record=invoice,
                roles=(ROLE_ACCOUNTS, ROLE_CEO),
                priority="urgent",
                due_date=invoice.due_date,
                target_url=_safe_reverse("invoice_view", invoice.pk),
                record_label=label,
                recipient_rows=accounts_recipients,
            )

        stale = AutomationNotification.objects.filter(is_resolved=False).filter(
            Q(source_key__startswith="operations:production_due:")
            | Q(source_key__startswith="operations:ready_to_ship:")
            | Q(source_key__startswith="operations:shipment_due:")
            | Q(source_key__startswith="operations:invoice_overdue:")
        )
        if active_keys:
            stale = stale.exclude(source_key__in=active_keys)
        stale.update(is_resolved=True, resolved_at=timezone.now())
        cache.set(cache_key, True, 900)
        return {"active": len(active_keys), "error": "", "cached": False}
    except (OperationalError, ProgrammingError) as exc:
        return {"active": 0, "error": str(exc)}
    except Exception as exc:
        logger.exception("Operations notification sync failed")
        return {"active": 0, "error": str(exc)}


def visible_notifications(user):
    base = AutomationNotification.objects.filter(is_resolved=False).exclude(
        source_key__startswith="crm-auto:invoice_overdue:"
    )
    if not user or not getattr(user, "is_authenticated", False):
        return AutomationNotification.objects.none()
    if getattr(user, "is_superuser", False):
        return base
    roles = operations_role_names(user)
    legacy_rule_types = {"general"}
    if can_access_operations_module(user, "invoices"):
        legacy_rule_types.add("invoice")
    if can_access_operations_module(user, "production"):
        legacy_rule_types.add("production")
    if can_access_operations_module(user, "inventory"):
        legacy_rule_types.add("inventory")
    if any(
        can_access_operations_module(user, module)
        for module in ("quotations", "production", "invoices")
    ):
        legacy_rule_types.add("lifecycle")
    return base.filter(
        Q(assigned_user=user)
        | Q(assigned_user__isnull=True, assigned_role__in=roles)
        | Q(assigned_user__isnull=True, assigned_role="", rule_type__in=legacy_rule_types)
    )
