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
    try:
        lifecycle = refresh_lifecycle(lifecycle)
    except Exception:
        messages.warning(request, "Lifecycle financials could not be refreshed. Showing last saved values.")

    can_view_profit = can_view_lifecycle_profit(request.user)
    profit_breakdown = None
    if can_view_profit:
        try:
            profit_breakdown = build_lifecycle_profit_breakdown(lifecycle)
        except Exception:
            can_view_profit = False
            messages.warning(request, "Lifecycle profit details could not be loaded.")
    steps = lifecycle_timeline_steps(lifecycle)

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
