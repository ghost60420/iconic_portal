from dataclasses import dataclass

from django.db.models import Q

from crm.models import CostingHeader, QuickCosting


QUEUE_CURRENCIES = {"CAD", "USD", "BDT"}
QUEUE_STATUS_PENDING = "pending"
QUEUE_STATUS_APPROVED = "approved"
QUEUE_STATUS_REJECTED = "rejected"
QUEUE_STATUS_RECALL_REQUESTED = "recall_requested"
QUEUE_STATUS_SENT = "sent"
QUEUE_STATUS_ACCEPTED = "accepted"
QUEUE_STATUS_CONVERTED = "converted"
QUEUE_STATUS_ALL = "all"
QUEUE_STATUS_FILTERS = {
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_APPROVED,
    QUEUE_STATUS_REJECTED,
    QUEUE_STATUS_RECALL_REQUESTED,
    QUEUE_STATUS_SENT,
    QUEUE_STATUS_ACCEPTED,
    QUEUE_STATUS_CONVERTED,
    QUEUE_STATUS_ALL,
}


@dataclass(frozen=True)
class CEOApprovalQueueQuerysets:
    advanced_qs: object
    quick_qs: object
    status_filter: str
    currency: str


def _advanced_base_queryset(for_rows=False):
    qs = CostingHeader.objects.filter(is_archived=False).exclude(quotation_number="")
    if for_rows:
        qs = (
            qs.select_related(
                "opportunity",
                "opportunity__lead",
                "opportunity__lead__assigned_to",
                "customer",
                "quoted_by",
                "approved_by",
                "quotation_approved_by",
                "quotation_rejected_by",
                "smv",
            )
            .prefetch_related("invoices", "line_items")
            .order_by("-quoted_at", "-updated_at", "-id")
        )
    return qs


def _quick_base_queryset(for_rows=False):
    qs = QuickCosting.objects.filter(
        Q(approval_submitted_at__isnull=False)
        | ~Q(status=QuickCosting.STATUS_DRAFT)
    )
    if for_rows:
        qs = (
            qs.select_related(
                "opportunity",
                "opportunity__assigned_to",
                "opportunity__lead",
                "opportunity__lead__assigned_to",
                "salesperson",
                "created_by",
                "approval_submitted_by",
                "approved_by",
                "rejected_by",
                "production_order",
            )
            .prefetch_related("invoices", "production_order__stages")
            .order_by("-approval_submitted_at", "-updated_at", "-id")
        )
    return qs


def build_ceo_approval_queue_querysets(
    *,
    status_filter=QUEUE_STATUS_PENDING,
    currency="",
    search="",
    date_from=None,
    date_to=None,
    for_rows=False,
):
    status_filter = (status_filter or QUEUE_STATUS_PENDING).strip()
    if status_filter not in QUEUE_STATUS_FILTERS:
        status_filter = QUEUE_STATUS_PENDING

    currency = (currency or "").strip().upper()
    if currency not in QUEUE_CURRENCIES:
        currency = ""

    search = (search or "").strip()
    advanced_qs = _advanced_base_queryset(for_rows=for_rows)
    quick_qs = _quick_base_queryset(for_rows=for_rows)

    if currency:
        advanced_qs = advanced_qs.filter(currency=currency)
        quick_qs = quick_qs.filter(currency=currency)

    if date_from:
        advanced_qs = advanced_qs.filter(quoted_at__date__gte=date_from)
        quick_qs = quick_qs.filter(approval_submitted_at__date__gte=date_from)
    if date_to:
        advanced_qs = advanced_qs.filter(quoted_at__date__lte=date_to)
        quick_qs = quick_qs.filter(approval_submitted_at__date__lte=date_to)

    if status_filter == QUEUE_STATUS_PENDING:
        advanced_qs = advanced_qs.filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
            invoices__isnull=True,
        )
        quick_qs = quick_qs.filter(invoices__isnull=True).filter(
            Q(
                approval_submitted_at__isnull=False,
                status__in=[QuickCosting.STATUS_SUBMITTED, QuickCosting.STATUS_DRAFT],
            )
            | Q(status=QuickCosting.STATUS_RECALL_REQUESTED)
        )
    elif status_filter == QUEUE_STATUS_APPROVED:
        advanced_qs = advanced_qs.filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            invoices__isnull=True,
        )
        quick_qs = quick_qs.filter(
            status__in=[QuickCosting.STATUS_APPROVED, QuickCosting.STATUS_QUOTED],
            invoices__isnull=True,
        )
    elif status_filter == QUEUE_STATUS_REJECTED:
        advanced_qs = advanced_qs.filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_REJECTED
        )
        quick_qs = quick_qs.filter(status=QuickCosting.STATUS_REJECTED)
    elif status_filter == QUEUE_STATUS_RECALL_REQUESTED:
        advanced_qs = advanced_qs.none()
        quick_qs = quick_qs.filter(status=QuickCosting.STATUS_RECALL_REQUESTED)
    elif status_filter == QUEUE_STATUS_SENT:
        advanced_qs = advanced_qs.filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_SENT
        )
        quick_qs = quick_qs.filter(status=QuickCosting.STATUS_QUOTED)
    elif status_filter == QUEUE_STATUS_ACCEPTED:
        advanced_qs = advanced_qs.filter(
            quotation_status=CostingHeader.QUOTATION_STATUS_ACCEPTED
        )
        quick_qs = quick_qs.none()
    elif status_filter == QUEUE_STATUS_CONVERTED:
        advanced_qs = advanced_qs.filter(invoices__isnull=False).distinct()
        quick_qs = quick_qs.filter(
            Q(invoices__isnull=False) | Q(status=QuickCosting.STATUS_INVOICED)
        ).distinct()

    if search:
        advanced_qs = advanced_qs.filter(
            Q(quotation_number__icontains=search)
            | Q(opportunity__opportunity_id__icontains=search)
            | Q(opportunity__lead__lead_id__icontains=search)
            | Q(customer__account_brand__icontains=search)
            | Q(customer__contact_name__icontains=search)
            | Q(style_name__icontains=search)
        )
        quick_qs = quick_qs.filter(
            Q(quotation_number__icontains=search)
            | Q(opportunity__opportunity_id__icontains=search)
            | Q(opportunity__lead__lead_id__icontains=search)
            | Q(account_brand__icontains=search)
            | Q(contact_name__icontains=search)
            | Q(buyer_name__icontains=search)
            | Q(project_name__icontains=search)
        )

    return CEOApprovalQueueQuerysets(
        advanced_qs=advanced_qs,
        quick_qs=quick_qs,
        status_filter=status_filter,
        currency=currency,
    )


def count_ceo_approval_queue_items(**filters):
    querysets = build_ceo_approval_queue_querysets(**filters)
    advanced_ids = querysets.advanced_qs.order_by().values_list("pk", flat=True)
    quick_ids = querysets.quick_qs.order_by().values_list("pk", flat=True)
    return advanced_ids.union(quick_ids, all=True).count()
