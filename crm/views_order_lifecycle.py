from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import OrderLifecycle
from .services.order_lifecycle import (
    build_lifecycle_profit_breakdown,
    can_view_lifecycle_profit,
    lifecycle_timeline_steps,
    refresh_lifecycle,
)


def _sanitize_lifecycle_for_context(lifecycle):
    lifecycle.estimated_revenue = None
    lifecycle.estimated_cost = None
    lifecycle.estimated_profit = None
    lifecycle.estimated_margin = None
    invoice = getattr(lifecycle, "invoice", None)
    if invoice:
        invoice.sewing_charge = 0
        invoice.other_internal_cost = 0
        invoice.internal_cost_note = ""
    return lifecycle


@login_required
def order_lifecycle_detail(request, pk):
    lifecycle = get_object_or_404(
        OrderLifecycle.objects.select_related(
            "customer",
            "lead",
            "opportunity",
            "costing",
            "quotation",
            "invoice",
            "production_order",
            "shipping_record",
            "created_by",
        ),
        pk=pk,
    )
    can_view_profit = can_view_lifecycle_profit(request.user)
    profit_breakdown = None
    if can_view_profit:
        try:
            lifecycle = refresh_lifecycle(lifecycle)
            profit_breakdown = build_lifecycle_profit_breakdown(lifecycle)
        except Exception:
            can_view_profit = False
            messages.warning(request, "Lifecycle profit details could not be loaded.")
    if not can_view_profit:
        lifecycle = _sanitize_lifecycle_for_context(lifecycle)
    steps = lifecycle_timeline_steps(lifecycle, include_amounts=can_view_profit)
    current_stage = lifecycle.status or "lead"
    for step in steps:
        step["is_current"] = step.get("key") == current_stage

    return render(
        request,
        "crm/order_lifecycle/detail.html",
        {
            "lifecycle": lifecycle,
            "steps": steps,
            "profit_breakdown": profit_breakdown,
            "can_view_profit": can_view_profit,
        },
    )
