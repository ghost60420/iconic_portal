from datetime import timedelta

from django.db.models import Count, F, Q, Window
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMAuditLog, CostingHeader, Invoice, Lead, ProductionOrder, Shipment
from crm.services.operations_formatting import activity_time_label, initials_for_name
from crm.services.employee_profiles import employee_display_name
from crm.services.operations_notifications import visible_notifications
from crm.services.operations_permissions import (
    ROLE_CEO,
    can_access_operations_module,
    has_operations_role,
    scope_sales_leads,
)


def operations_dashboard_context(user, *, today=None):
    today = today or timezone.localdate()
    allowed_modules = [
        module
        for module in ("leads", "opportunities", "quotations", "production", "invoices", "payments", "finance", "quick_costing")
        if can_access_operations_module(user, "invoices" if module == "payments" else module)
    ]
    activity_rows = list(
        CRMAuditLog.objects.filter(module__in=allowed_modules)
        .select_related("actor", "actor__employee_profile")
        .order_by("-created_at", "-id")[:25]
    )
    module_labels = {
        "leads": "Lead",
        "opportunities": "Opportunity",
        "quotations": "Quotation",
        "production": "Production Order",
        "invoices": "Invoice",
        "payments": "Payment",
        "finance": "Finance Entry",
        "quick_costing": "Quick Costing",
    }
    recent_activity = []
    for row in activity_rows:
        actor_name = employee_display_name(row.actor)
        record_label = row.record_label or row.record_id
        action_label = row.get_action_type_display().lower()
        recent_activity.append(
            {
                "actor_name": actor_name,
                "actor_initials": initials_for_name(actor_name),
                "sentence": f"{actor_name} {action_label} {module_labels.get(row.module, row.module.title())} {record_label}",
                "module": module_labels.get(row.module, row.module.title()),
                "target_url": row.target_url,
                "time_label": activity_time_label(row.created_at),
            }
        )

    today_tasks = []
    upcoming_deliveries = []
    pending_approvals = []
    metric_cards = []

    if can_access_operations_module(user, "leads"):
        followups = scope_sales_leads(
            Lead.objects.filter(is_archived=False).filter(
                Q(next_followup=today) | Q(next_follow_up_date=today)
            ),
            user,
        ).only("id", "lead_id", "account_brand", "contact_name")[:8]
        for lead in followups:
            today_tasks.append(
                {
                    "type": "Follow up",
                    "title": lead.account_brand or lead.contact_name or lead.lead_id,
                    "detail": lead.lead_id,
                    "url": reverse("lead_detail", args=[lead.pk]),
                    "tone": "normal",
                }
            )

    if can_access_operations_module(user, "quotations"):
        quotations = CostingHeader.objects.select_related("customer", "opportunity", "quoted_by").filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
        ).exclude(quotation_number="")
        if has_operations_role(user, "Sales") and not has_operations_role(user, ROLE_CEO):
            quotations = quotations.filter(quoted_by=user)
        quotation_rows = list(
            quotations.annotate(operations_total=Window(expression=Count("id")))
            .order_by("quoted_at", "id")[:8]
        )
        pending_quote_count = quotation_rows[0].operations_total if quotation_rows else 0
        for quote in quotation_rows:
            row = {
                "type": "CEO approval",
                "title": quote.quotation_number,
                "detail": (quote.customer.account_brand if quote.customer else "") or quote.style_name,
                "url": reverse("cost_sheet_client_quotation", args=[quote.pk]),
                "tone": "high",
            }
            pending_approvals.append(row)
            today_tasks.append(row)
        metric_cards.append(
            {
                "label": "Pending CEO Approvals",
                "count": pending_quote_count,
                "url": reverse("operations_queue", args=["pending-ceo-approvals"]),
                "tone": "gold",
            }
        )

    if can_access_operations_module(user, "production"):
        unfinished = ~Q(operational_status__in=["shipped", "cancelled"])
        production_counts = ProductionOrder.objects.filter(is_archived=False).aggregate(
            due_today=Count("id", filter=Q(bulk_deadline=today) & unfinished),
            late=Count("id", filter=Q(bulk_deadline__lt=today) & unfinished),
            ready=Count("id", filter=Q(operational_status="ready_to_ship")),
        )
        metric_cards.extend(
            [
                {
                    "label": "Production Due Today",
                    "count": production_counts["due_today"],
                    "url": reverse("operations_queue", args=["production-due-today"]),
                    "tone": "blue",
                },
                {
                    "label": "Late Production Orders",
                    "count": production_counts["late"],
                    "url": reverse("operations_queue", args=["late-production"]),
                    "tone": "red",
                },
                {
                    "label": "Ready to Ship",
                    "count": production_counts["ready"],
                    "url": f'{reverse("production_list")}?status=ready_to_ship',
                    "tone": "green",
                },
            ]
        )
        orders = list(
            ProductionOrder.objects.select_related("customer", "assigned_production_manager")
            .filter(is_archived=False, bulk_deadline__range=(today, today + timedelta(days=14)))
            .exclude(operational_status__in=["shipped", "cancelled"])
            .order_by("bulk_deadline", "id")[:12]
        )
        for order in orders:
            row = {
                "type": "Delivery",
                "title": order.order_code or order.title,
                "detail": f"Due {order.bulk_deadline:%b %d, %Y} | {order.get_operational_status_display()}",
                "url": reverse("production_detail", args=[order.pk]),
                "tone": "high" if order.bulk_deadline <= today + timedelta(days=3) else "normal",
            }
            upcoming_deliveries.append(row)
            if order.bulk_deadline <= today + timedelta(days=3):
                today_tasks.append(row)

        ready_orders = ProductionOrder.objects.filter(
            is_archived=False,
            operational_status="ready_to_ship",
        ).only("id", "order_code", "title")[:6]
        for order in ready_orders:
            today_tasks.append(
                {
                    "type": "Shipment",
                    "title": order.order_code or order.title,
                    "detail": "Ready to ship",
                    "url": reverse("production_detail", args=[order.pk]),
                    "tone": "high",
                }
            )

        shipments = (
            Shipment.objects.select_related("order")
            .filter(ship_date__range=(today, today + timedelta(days=3)))
            .exclude(status__in=["shipped", "out_for_delivery", "delivered", "cancelled"])
            .order_by("ship_date", "id")[:6]
        )
        for shipment in shipments:
            today_tasks.append(
                {
                    "type": "Shipment",
                    "title": shipment.order.order_code if shipment.order else f"Shipment {shipment.pk}",
                    "detail": f"Ships {shipment.ship_date:%b %d, %Y}",
                    "url": reverse("shipment_detail", args=[shipment.pk]),
                    "tone": "high",
                }
            )

    if can_access_operations_module(user, "invoices"):
        overdue_queryset = (
            Invoice.objects.select_related("customer")
            .exclude(status__in=["paid", "cancelled"])
            .filter(due_date__lt=today, total_amount__gt=F("paid_amount"))
        )
        overdue_count = overdue_queryset.count()
        for invoice in overdue_queryset.order_by("due_date", "id")[:8]:
            today_tasks.append(
                {
                    "type": "Overdue invoice",
                    "title": invoice.invoice_number,
                    "detail": f"Due {invoice.due_date:%b %d, %Y}",
                    "url": reverse("invoice_view", args=[invoice.pk]),
                    "tone": "urgent",
                }
            )
        metric_cards.append(
            {
                "label": "Invoices Overdue",
                "count": overdue_count,
                "url": reverse("operations_queue", args=["invoices-overdue"]),
                "tone": "red",
            }
        )

        drafts = Invoice.objects.filter(invoice_status="DRAFT").only("id", "invoice_number", "issue_date")[:8]
        for invoice in drafts:
            pending_approvals.append(
                {
                    "type": "Invoice approval",
                    "title": invoice.invoice_number,
                    "detail": f"Created {invoice.issue_date:%b %d, %Y}",
                    "url": reverse("invoice_view", args=[invoice.pk]),
                    "tone": "normal",
                }
            )

    unread_count = visible_notifications(user).filter(is_read=False).count()
    metric_cards.append(
        {
            "label": "Unread Notifications",
            "count": unread_count,
            "url": f'{reverse("notification_list")}?status=unread',
            "tone": "gold",
        }
    )

    tone_rank = {"urgent": 0, "high": 1, "normal": 2}
    today_tasks.sort(key=lambda row: tone_rank.get(row["tone"], 3))
    return {
        "operations_recent_activity": recent_activity,
        "operations_today_tasks": today_tasks[:20],
        "operations_upcoming_deliveries": upcoming_deliveries[:12],
        "operations_pending_approvals": pending_approvals[:12],
        "operations_metric_cards": metric_cards,
    }
