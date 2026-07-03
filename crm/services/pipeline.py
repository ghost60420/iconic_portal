from collections import defaultdict
from decimal import Decimal

from django.db import models
from django.db.models import Case, Count, F, OuterRef, Subquery, Sum, When
from django.db.models.functions import Coalesce

from crm.models import CostingHeader, Opportunity, QuickCosting
from crm.services.costing_currency import currency_summary_rows


CLOSED_PIPELINE_STAGES = ("Closed Won", "Closed Lost", "Cancelled")
PIPELINE_QUICK_COSTING_STATUSES = (
    QuickCosting.STATUS_APPROVED,
    QuickCosting.STATUS_QUOTED,
    QuickCosting.STATUS_INVOICED,
    QuickCosting.STATUS_PRODUCTION,
    QuickCosting.STATUS_SHIPPED,
    QuickCosting.STATUS_CLOSED,
)


def open_pipeline_queryset(queryset=None):
    """Return the single CRM definition of an open pipeline opportunity."""
    queryset = queryset if queryset is not None else Opportunity.objects.all()
    return (
        queryset.filter(is_archived=False, is_open=True)
        .exclude(stage__in=CLOSED_PIPELINE_STAGES)
    )


def with_pipeline_value(queryset, annotation_name="pipeline_value"):
    """Annotate opportunities with the one display value used by pipeline surfaces."""
    revenue_expression = models.ExpressionWrapper(
        F("selling_price_per_piece") * F("quantity"),
        output_field=models.DecimalField(max_digits=16, decimal_places=2),
    )
    approved_quick = (
        QuickCosting.objects.filter(
            opportunity=OuterRef("pk"),
            status__in=PIPELINE_QUICK_COSTING_STATUSES,
        )
        .annotate(_pipeline_revenue=revenue_expression)
        .order_by("-updated_at", "-id")
    )
    latest_quick = (
        QuickCosting.objects.filter(opportunity=OuterRef("pk"))
        .annotate(_pipeline_revenue=revenue_expression)
        .order_by("-updated_at", "-id")
    )
    approved_advanced = CostingHeader.objects.filter(
        opportunity=OuterRef("pk"), status="approved", is_archived=False
    ).order_by("-updated_at", "-id")
    latest_advanced = CostingHeader.objects.filter(
        opportunity=OuterRef("pk"), is_archived=False
    ).order_by("-updated_at", "-id")
    annotated = queryset.annotate(
        _pipeline_quick_value=Coalesce(
            Subquery(approved_quick.values("_pipeline_revenue")[:1]),
            Subquery(latest_quick.values("_pipeline_revenue")[:1]),
            output_field=models.DecimalField(max_digits=16, decimal_places=2),
        ),
        _pipeline_quick_currency=Coalesce(
            Subquery(approved_quick.values("currency")[:1]),
            Subquery(latest_quick.values("currency")[:1]),
            models.Value("BDT"),
            output_field=models.CharField(max_length=10),
        ),
        _pipeline_advanced_quantity=Coalesce(
            Subquery(approved_advanced.values("order_quantity")[:1]),
            Subquery(latest_advanced.values("order_quantity")[:1]),
            output_field=models.IntegerField(),
        ),
        _pipeline_advanced_currency=Coalesce(
            Subquery(approved_advanced.values("currency")[:1]),
            Subquery(latest_advanced.values("currency")[:1]),
            output_field=models.CharField(max_length=10),
        ),
    ).annotate(
        _pipeline_advanced_value=models.ExpressionWrapper(
            F("costing_fob_per_piece") * F("_pipeline_advanced_quantity"),
            output_field=models.DecimalField(max_digits=16, decimal_places=2),
        )
    )
    return annotated.annotate(
        **{
            annotation_name: Coalesce(
                F("_pipeline_quick_value"),
                F("_pipeline_advanced_value"),
                F("order_value_usd"),
                F("order_value"),
                models.Value(Decimal("0")),
                output_field=models.DecimalField(max_digits=16, decimal_places=2),
            ),
            "pipeline_currency": Case(
                When(_pipeline_quick_value__isnull=False, then=F("_pipeline_quick_currency")),
                When(_pipeline_advanced_value__isnull=False, then=F("_pipeline_advanced_currency")),
                When(order_value_usd__isnull=False, then=models.Value("USD")),
                default=Coalesce(F("order_currency"), models.Value("CAD")),
                output_field=models.CharField(max_length=10),
            ),
        }
    )


def summarize_pipeline(queryset=None, *, apply_open_definition=True):
    """Return the shared count and native-currency totals for pipeline widgets."""
    queryset = queryset if queryset is not None else Opportunity.objects.all()
    if apply_open_definition:
        queryset = open_pipeline_queryset(queryset)
    grouped = (
        with_pipeline_value(queryset)
        .values("pipeline_currency")
        .annotate(amount=Sum("pipeline_value"), count=Count("id"))
    )
    totals = defaultdict(lambda: {"amount": Decimal("0")})
    count = 0
    for row in grouped:
        currency = (row.get("pipeline_currency") or "CAD").upper()
        totals[currency]["amount"] += row.get("amount") or Decimal("0")
        count += int(row.get("count") or 0)
    return {"count": count, "rows": currency_summary_rows(totals)}
