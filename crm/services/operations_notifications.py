import logging

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Concat
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from crm.audit_context import get_current_actor
from crm.models import AutomationNotification, CostingHeader, Invoice, Lead, Opportunity, ProductionOrder, QuickCosting, Shipment
from crm.services.operations_formatting import relative_time_label
from crm.services.operations_permissions import (
    ROLE_ACCOUNTS,
    ROLE_ADMIN,
    ROLE_CEO,
    ROLE_MANAGER,
    ROLE_PRODUCTION,
    ROLE_SALES,
    can_access_operations_module,
    has_operations_role,
    operations_role_names,
    scope_sales_leads,
    scope_sales_opportunities,
)


logger = logging.getLogger(__name__)
PERIODIC_SOURCE_PREFIXES = (
    "operations:sample_due:",
    "operations:production_overdue:",
    "operations:shipment_due_today:",
    "operations:shipment_delayed:",
    "operations:invoice_overdue:",
)
LEGACY_PERIODIC_SOURCE_PREFIXES = (
    "operations:production_due:",
    "operations:ready_to_ship:",
    "operations:shipment_due:",
)
NOTIFICATION_ICON_SYMBOLS = {
    "ceo_approval": "✔",
    "ceo_approved": "✔",
    "ceo_rejected": "⚠",
    "mention": "@",
    "production_created": "🏭",
    "sample_due": "🏭",
    "production_due": "🏭",
    "shipment_due": "🚚",
    "shipment_delayed": "⚠",
    "invoice_overdue": "💰",
    "task_assigned": "📋",
    "task_completed": "📋",
    "comment_added": "💬",
    "general": "⏰",
}


def prepare_notification_display(item):
    item.icon_symbol = "🔴" if item.priority == "critical" else NOTIFICATION_ICON_SYMBOLS.get(
        item.notification_type, "⏰"
    )
    item.age_label = relative_time_label(item.created_at)
    item.open_url = reverse("notification_open", args=[item.pk])
    return item


def notification_priority_order():
    return models.Case(
        models.When(priority="critical", then=models.Value(0)),
        models.When(priority="high", then=models.Value(1)),
        models.When(priority="normal", then=models.Value(2)),
        models.When(priority="information", then=models.Value(3)),
        default=models.Value(4),
        output_field=models.IntegerField(),
    )


def _safe_reverse(name, pk):
    try:
        return reverse(name, args=[pk])
    except NoReverseMatch:
        return ""


def _recipient_rows(role_names):
    User = get_user_model()
    roles = tuple(dict.fromkeys(role_names))
    users = list(
        User.objects.filter(is_active=True)
        .filter(Q(groups__name__in=roles) | Q(is_superuser=True))
        .distinct()
        .only("id", "is_active", "is_superuser")
    )
    if users:
        return [(user, "") for user in users]
    return [(None, role) for role in roles]


def _combined_recipient_rows(*, users=(), roles=(), exclude_user=None):
    excluded_id = getattr(exclude_user, "pk", None)
    rows = []
    seen_users = set()
    for user in users:
        if not user or not getattr(user, "is_active", False) or user.pk == excluded_id:
            continue
        if user.pk not in seen_users:
            seen_users.add(user.pk)
            rows.append((user, ""))
    for user, role in _recipient_rows(roles) if roles else ():
        if user:
            if user.pk == excluded_id or user.pk in seen_users:
                continue
            seen_users.add(user.pk)
        rows.append((user, role))
    return rows


def _clear_header_cache(user_ids):
    for user_id in set(user_ids):
        if user_id:
            cache.delete(f"crm-header-unread:{user_id}")


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
    touched_users = []
    rows = recipient_rows if recipient_rows is not None else _recipient_rows(roles)
    for user, role in rows:
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
        touched_users.append(getattr(user, "pk", None))
    _clear_header_cache(touched_users)
    return active_keys


def _active_user_for_label(label):
    label = " ".join((label or "").split())
    if not label:
        return None
    User = get_user_model()
    candidates = list(
        User.objects.filter(is_active=True)
        .annotate(task_full_name=Concat("first_name", models.Value(" "), "last_name"))
        .filter(
            Q(username__iexact=label)
            | Q(first_name__iexact=label)
            | Q(employee_profile__display_name__iexact=label)
            | Q(task_full_name__iexact=label)
        )
        .select_related("employee_profile")[:10]
    )
    folded = label.casefold()
    for user in candidates:
        values = {
            user.get_username().casefold(),
            user.first_name.casefold(),
            user.get_full_name().strip().casefold(),
            user.employee_profile.public_name.casefold(),
        }
        if folded in values:
            return user
    return None


def _lead_owner(lead):
    if not lead:
        return None
    return lead.assigned_to or _active_user_for_label(lead.owner)


def notify_quotation_waiting_approval(costing):
    is_quick = isinstance(costing, QuickCosting)
    label = costing.quotation_number or (f"Quick Costing QC-{costing.pk}" if is_quick else f"Quotation {costing.pk}")
    source_key = (
        f"operations:ceo_approval:quick:{costing.pk}"
        if is_quick
        else f"operations:ceo_approval:{costing.pk}"
    )
    return create_operations_notification(
        source_key=source_key,
        notification_type="ceo_approval",
        title="CEO approval required",
        message=f"{label} is ready for CEO review.",
        related_module="quick_costing" if is_quick else "lifecycle",
        record=costing,
        roles=(ROLE_CEO,),
        priority="high",
        due_date=timezone.localdate(),
        target_url=_safe_reverse("quick_costing_detail" if is_quick else "cost_sheet_client_quotation", costing.pk),
        record_label=label,
    )


def notify_quotation_decision(costing, decision):
    if decision not in {"approved", "rejected"}:
        return set()
    is_quick = isinstance(costing, QuickCosting)
    label = costing.quotation_number or (f"Quick Costing QC-{costing.pk}" if is_quick else f"Quotation {costing.pk}")
    actor = (
        costing.approved_by if is_quick and decision == "approved"
        else costing.rejected_by if is_quick
        else costing.quotation_approved_by if decision == "approved"
        else costing.quotation_rejected_by
    )
    lead = costing.opportunity.lead if costing.opportunity_id else None
    submitter = costing.approval_submitted_by if is_quick else costing.quoted_by
    recipients = _combined_recipient_rows(
        users=(submitter, _lead_owner(lead)),
        exclude_user=actor,
    )
    source_key = (
        f"operations:ceo_{decision}:quick:{costing.pk}"
        if is_quick
        else f"operations:ceo_{decision}:{costing.pk}"
    )
    keys = create_operations_notification(
        source_key=source_key,
        notification_type=f"ceo_{decision}",
        title=f"Quotation {decision} by CEO",
        message=f"{label} was {decision}.",
        related_module="quick_costing" if is_quick else "lifecycle",
        record=costing,
        priority="normal" if decision == "approved" else "high",
        target_url=_safe_reverse("quick_costing_detail" if is_quick else "cost_sheet_client_quotation", costing.pk),
        record_label=label,
        recipient_rows=recipients,
    )
    if decision == "approved":
        keys |= create_operations_notification(
            source_key=f"operations:accounting_approved:{'quick' if is_quick else 'advanced'}:{costing.pk}",
            notification_type="ceo_approved",
            title="CEO-approved costing ready",
            message=f"{label} is ready for Accounting.",
            related_module="invoices",
            record=costing,
            roles=(ROLE_ACCOUNTS,),
            priority="normal",
            target_url=f"{reverse('ceo_quotation_approval_queue')}?status=approved",
            record_label=label,
        )
    return keys


def notify_production_order_created(order):
    label = order.purchase_order_number or order.title or f"Production Order {order.pk}"
    quoted_by = order.source_quotation.quoted_by if order.source_quotation_id else None
    recipients = _combined_recipient_rows(
        users=(order.assigned_production_manager, quoted_by, _lead_owner(order.lead)),
        roles=(ROLE_PRODUCTION,),
        exclude_user=order.created_by,
    )
    return create_operations_notification(
        source_key=f"operations:production_created:{order.pk}",
        notification_type="production_created",
        title="Production Order created",
        message=f"{label} is ready for production planning.",
        related_module="production",
        record=order,
        priority="normal",
        due_date=order.bulk_deadline,
        target_url=_safe_reverse("production_detail", order.pk),
        record_label=label,
        recipient_rows=recipients,
    )


def _task_context(task):
    if hasattr(task, "lead"):
        lead = task.lead
        return lead, _lead_owner(lead), lead.lead_id or f"Lead {lead.pk}", _safe_reverse("lead_detail", lead.pk), "leads"
    opportunity = task.opportunity
    lead = opportunity.lead
    return (
        opportunity,
        _lead_owner(lead),
        opportunity.opportunity_id or f"Opportunity {opportunity.pk}",
        _safe_reverse("opportunity_detail", opportunity.pk),
        "lifecycle",
    )


def notify_task_event(task, event):
    if event not in {"assigned", "completed"}:
        return set()
    actor = get_current_actor()
    assigned_user = _active_user_for_label(task.assigned_to)
    record, owner, record_label, target_url, related_module = _task_context(task)
    if event == "assigned":
        recipients = _combined_recipient_rows(users=(assigned_user,), exclude_user=actor)
        priority = {"urgent": "critical", "high": "high"}.get((task.priority or "").casefold(), "normal")
        title = "Task assigned to you"
        message = f"{task.title} was assigned on {record_label}."
    else:
        recipients = _combined_recipient_rows(users=(owner or assigned_user,), exclude_user=actor)
        priority = "information"
        title = "Task completed"
        message = f"{task.title} was completed on {record_label}."
    return create_operations_notification(
        source_key=f"operations:task_{event}:{task._meta.label_lower}:{task.pk}",
        notification_type=f"task_{event}",
        title=title,
        message=message,
        related_module=related_module,
        record=record,
        priority=priority,
        due_date=task.due_date,
        target_url=target_url,
        record_label=record_label,
        recipient_rows=recipients,
    )


def notify_comment_added(comment):
    actor = comment.author_user
    if not actor or comment.is_ai:
        return set()
    if comment.production_id:
        record = comment.production
        owner = record.assigned_production_manager
        label = record.purchase_order_number or record.title or f"Production Order {record.pk}"
        target_url = _safe_reverse("production_detail", record.pk)
        related_module = "production"
    elif comment.opportunity_id:
        record = comment.opportunity
        owner = _lead_owner(record.lead)
        label = record.opportunity_id or f"Opportunity {record.pk}"
        target_url = _safe_reverse("opportunity_detail", record.pk)
        related_module = "lifecycle"
    elif comment.lead_id:
        record = comment.lead
        owner = _lead_owner(record)
        label = record.lead_id or f"Lead {record.pk}"
        target_url = _safe_reverse("lead_detail", record.pk)
        related_module = "leads"
    else:
        return set()
    if not owner or owner.pk == actor.pk:
        return set()
    from crm.services.chatter_mentions import mention_handles

    owner_handles = {
        owner.get_username().casefold(),
        owner.first_name.casefold(),
        owner.employee_profile.public_name.split()[0].casefold(),
    }
    if owner_handles.intersection(handle.casefold() for handle in mention_handles(comment.content)):
        return set()
    preview = " ".join((comment.content or "").split())[:180]
    return create_operations_notification(
        source_key=f"operations:comment_added:{comment.pk}",
        notification_type="comment_added",
        title=f"New comment on {label}",
        message=preview,
        related_module=related_module,
        record=record,
        priority="information",
        target_url=target_url,
        record_label=label,
        recipient_rows=[(owner, "")],
    )


def _resolve_stale_periodic_notifications(active_keys):
    stale_filter = Q()
    for prefix in PERIODIC_SOURCE_PREFIXES + LEGACY_PERIODIC_SOURCE_PREFIXES:
        stale_filter |= Q(source_key__startswith=prefix)
    stale = AutomationNotification.objects.filter(is_resolved=False).filter(stale_filter)
    if active_keys:
        stale = stale.exclude(source_key__in=active_keys)
    user_ids = list(stale.exclude(assigned_user_id=None).values_list("assigned_user_id", flat=True).distinct())
    stale.update(is_resolved=True, resolved_at=timezone.now())
    _clear_header_cache(user_ids)


def sync_operations_notifications(today=None, *, force=False):
    today = today or timezone.localdate()
    cache_key = f"operations-notification-sync:{today.isoformat()}"
    if not force and cache.get(cache_key):
        return {"active": 0, "error": "", "cached": True}
    active_keys = set()
    try:
        production_recipients = _recipient_rows((ROLE_PRODUCTION, ROLE_CEO))
        accounts_recipients = _recipient_rows((ROLE_ACCOUNTS, ROLE_CEO))
        active_statuses = ~Q(operational_status__in=["shipped", "cancelled"])

        sample_orders = (
            ProductionOrder.objects.filter(is_archived=False, sample_deadline=today)
            .filter(active_statuses)
            .only("id", "order_code", "title", "sample_deadline")[:100]
        )
        for order in sample_orders:
            label = order.purchase_order_number or order.title or f"Production {order.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:sample_due:{order.pk}",
                notification_type="sample_due",
                title="Sample due today",
                message=f"{label} has a sample deadline today.",
                related_module="production",
                record=order,
                priority="high",
                due_date=order.sample_deadline,
                target_url=_safe_reverse("production_detail", order.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        overdue_orders = (
            ProductionOrder.objects.filter(is_archived=False, bulk_deadline__lt=today)
            .filter(active_statuses)
            .only("id", "order_code", "title", "bulk_deadline")[:100]
        )
        for order in overdue_orders:
            label = order.purchase_order_number or order.title or f"Production {order.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:production_overdue:{order.pk}",
                notification_type="production_due",
                title="Production overdue",
                message=f"{label} was due on {order.bulk_deadline:%b %d, %Y}.",
                related_module="production",
                record=order,
                priority="critical",
                due_date=order.bulk_deadline,
                target_url=_safe_reverse("production_detail", order.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        open_shipment_statuses = ["planned", "booked"]
        due_shipments = (
            Shipment.objects.select_related("order")
            .filter(ship_date=today, status__in=open_shipment_statuses)
            .only("id", "ship_date", "order__id", "order__order_code", "order__title")[:100]
        )
        for shipment in due_shipments:
            order = shipment.order
            label = (order.purchase_order_number or order.title) if order else f"Shipment {shipment.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:shipment_due_today:{shipment.pk}",
                notification_type="shipment_due",
                title="Shipment due today",
                message=f"{label} is scheduled to ship today.",
                related_module="production",
                record=shipment,
                priority="high",
                due_date=shipment.ship_date,
                target_url=_safe_reverse("shipment_detail", shipment.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        delayed_shipments = (
            Shipment.objects.select_related("order")
            .filter(ship_date__lt=today, status__in=open_shipment_statuses)
            .only("id", "ship_date", "order__id", "order__order_code", "order__title")[:100]
        )
        for shipment in delayed_shipments:
            order = shipment.order
            label = (order.purchase_order_number or order.title) if order else f"Shipment {shipment.pk}"
            active_keys |= create_operations_notification(
                source_key=f"operations:shipment_delayed:{shipment.pk}",
                notification_type="shipment_delayed",
                title="Shipment delayed",
                message=f"{label} missed its {shipment.ship_date:%b %d, %Y} ship date.",
                related_module="production",
                record=shipment,
                priority="critical",
                due_date=shipment.ship_date,
                target_url=_safe_reverse("shipment_detail", shipment.pk),
                record_label=label,
                recipient_rows=production_recipients,
            )

        overdue_invoices = (
            Invoice.objects.exclude(status__in=["paid", "cancelled"])
            .filter(due_date__lt=today, total_amount__gt=F("paid_amount"))
            .only("id", "invoice_number", "due_date")[:100]
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
                priority="critical",
                due_date=invoice.due_date,
                target_url=_safe_reverse("invoice_view", invoice.pk),
                record_label=label,
                recipient_rows=accounts_recipients,
            )

        _resolve_stale_periodic_notifications(active_keys)
        cache.set(cache_key, True, 900)
        return {"active": len(active_keys), "error": "", "cached": False}
    except (OperationalError, ProgrammingError) as exc:
        return {"active": 0, "error": str(exc)}
    except Exception as exc:
        logger.exception("Operations notification sync failed")
        return {"active": 0, "error": str(exc)}


def visible_notifications(user):
    base = AutomationNotification.objects.filter(is_resolved=False).exclude(
        source_key__startswith="crm-auto:"
    )
    if not user or not getattr(user, "is_authenticated", False):
        return AutomationNotification.objects.none()
    if getattr(user, "is_superuser", False):
        return base
    roles = operations_role_names(user)
    legacy_rule_types = {"general"}
    if can_access_operations_module(user, "leads"):
        legacy_rule_types.add("leads")
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
    queryset = base.filter(rule_type__in=legacy_rule_types).filter(
        Q(assigned_user=user)
        | Q(assigned_user__isnull=True, assigned_role__in=roles)
        | Q(assigned_user__isnull=True, assigned_role="", rule_type__in=legacy_rule_types)
    )
    if has_operations_role(user, ROLE_SALES) and not has_operations_role(
        user, ROLE_CEO, ROLE_MANAGER, ROLE_ADMIN
    ):
        lead_type = ContentType.objects.get_for_model(Lead)
        opportunity_type = ContentType.objects.get_for_model(Opportunity)
        costing_type = ContentType.objects.get_for_model(CostingHeader)
        scoped_leads = scope_sales_leads(Lead.objects.all(), user).values("pk")
        scoped_opportunities = scope_sales_opportunities(Opportunity.objects.all(), user).values("pk")
        scoped_costings = CostingHeader.objects.filter(
            opportunity_id__in=scope_sales_opportunities(Opportunity.objects.all(), user).values("pk")
        ).values("pk")
        scoped_types = (lead_type.pk, opportunity_type.pk, costing_type.pk)
        queryset = queryset.filter(
            ~Q(record_content_type_id__in=scoped_types)
            | Q(record_content_type=lead_type, record_object_id__in=scoped_leads)
            | Q(record_content_type=opportunity_type, record_object_id__in=scoped_opportunities)
            | Q(record_content_type=costing_type, record_object_id__in=scoped_costings)
        )
    return queryset


def filter_notifications_by_search(queryset, query):
    query = " ".join((query or "").split())[:100]
    if not query:
        return queryset
    matches = (
        Q(title__icontains=query)
        | Q(message__icontains=query)
        | Q(record_label__icontains=query)
        | Q(assigned_user__username__icontains=query)
        | Q(assigned_user__first_name__icontains=query)
        | Q(assigned_user__last_name__icontains=query)
        | Q(assigned_user__employee_profile__display_name__icontains=query)
    )
    record_queries = (
        (
            Lead,
            Lead.objects.filter(
                Q(lead_id__icontains=query)
                | Q(account_brand__icontains=query)
                | Q(contact_name__icontains=query)
                | Q(email__icontains=query)
            ).values("pk"),
        ),
        (
            Opportunity,
            Opportunity.objects.filter(
                Q(opportunity_id__icontains=query)
                | Q(lead__lead_id__icontains=query)
                | Q(lead__account_brand__icontains=query)
                | Q(customer__account_brand__icontains=query)
            ).values("pk"),
        ),
        (
            CostingHeader,
            CostingHeader.objects.filter(
                Q(quotation_number__icontains=query)
                | Q(style_name__icontains=query)
                | Q(style_code__icontains=query)
                | Q(brand__icontains=query)
                | Q(customer__account_brand__icontains=query)
            ).values("pk"),
        ),
        (
            ProductionOrder,
            ProductionOrder.objects.filter(
                ProductionOrder.identifier_search_query(query)
                | Q(title__icontains=query)
                | Q(client_name_snapshot__icontains=query)
                | Q(brand_name_snapshot__icontains=query)
                | Q(customer__account_brand__icontains=query)
                | Q(quotation_number_snapshot__icontains=query)
            ).values("pk"),
        ),
        (
            Invoice,
            Invoice.objects.filter(
                Q(invoice_number__icontains=query)
                | Q(customer__account_brand__icontains=query)
                | Q(customer__contact_name__icontains=query)
            ).values("pk"),
        ),
        (
            Shipment,
            Shipment.objects.filter(
                Q(tracking_number__icontains=query)
                | ProductionOrder.identifier_search_query(query, "order__order_code")
                | Q(customer__account_brand__icontains=query)
            ).values("pk"),
        ),
    )
    for model_class, ids in record_queries:
        content_type = ContentType.objects.get_for_model(model_class)
        matches |= Q(record_content_type=content_type, record_object_id__in=ids)
    return queryset.filter(matches).distinct()
