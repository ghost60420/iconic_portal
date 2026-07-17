from django.db.models import Exists, OuterRef

from crm.models import Invoice, Opportunity, ProductionOrder, QuickCosting


def orphan_production_opportunities_queryset():
    """Opportunities marked Production without a linked ProductionOrder."""
    production_exists = ProductionOrder.objects.filter(opportunity_id=OuterRef("pk"))
    return (
        Opportunity.objects.filter(stage="Production")
        .annotate(has_production_order=Exists(production_exists))
        .filter(has_production_order=False)
    )


def broken_production_state_count():
    return orphan_production_opportunities_queryset().count()


def production_integrity_rows(opportunities=None):
    opportunities = opportunities or orphan_production_opportunities_queryset()
    rows = []
    for opportunity in opportunities.select_related("customer", "lead").order_by("id"):
        quick_costing = (
            QuickCosting.objects.filter(opportunity=opportunity)
            .order_by("-updated_at", "-id")
            .first()
        )
        invoice = None
        if quick_costing:
            invoice = (
                Invoice.objects.filter(quick_costing=quick_costing)
                .order_by("-issue_date", "-created_at", "-id")
                .first()
            )
        if not invoice:
            invoice = (
                Invoice.objects.filter(opportunity=opportunity)
                .order_by("-issue_date", "-created_at", "-id")
                .first()
            )
        rows.append(
            {
                "opportunity": opportunity,
                "customer": opportunity.customer,
                "lead": opportunity.lead,
                "quick_costing": quick_costing,
                "invoice": invoice,
                "production_exists": ProductionOrder.objects.filter(opportunity=opportunity).exists(),
            }
        )
    return rows
