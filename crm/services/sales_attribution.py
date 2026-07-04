"""Single source of truth for lead-derived sales attribution and KPI values.

Salesperson attribution always follows the related Lead.  Creator/author fields
are intentionally separate and never affect commercial attribution.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import models
from django.core.cache import cache
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from crm.models import (
    CostingHeader,
    CRMAuditLog,
    Customer,
    EmployeeProfile,
    Invoice,
    InvoicePayment,
    Lead,
    LeadActivity,
    Opportunity,
    ProductionOrder,
    QuickCosting,
)
from crm.services.employee_identity import (
    build_employee_identity_index,
    canonical_employee_name,
    employee_lead_ownership_q,
    get_employee_identity_index,
    known_employee_owner_q,
    resolve_employee_identity,
)
from crm.services.pipeline import CLOSED_PIPELINE_STAGES, summarize_pipeline, with_pipeline_value


CURRENCIES = ("CAD", "USD", "BDT")
ZERO = Decimal("0")
ISSUED_INVOICE_STATUSES = ("sent", "partial", "paid")
ACTIVE_PRODUCTION_STATUSES = ("planning", "in_progress", "hold")


def _empty_rows():
    return {currency: {"amount": ZERO, "count": 0} for currency in CURRENCIES}


def _rows(grouped, *, amount_key="amount"):
    values = _empty_rows()
    for row in grouped:
        currency = (row.get("currency") or "").upper()
        if currency in values:
            values[currency]["amount"] += row.get(amount_key) or ZERO
            values[currency]["count"] += int(row.get("count") or 0)
    return [{"currency": currency, **values[currency]} for currency in CURRENCIES]


def lead_ownership_q(user, prefix=""):
    return employee_lead_ownership_q(user, prefix=prefix)


def production_ownership_q(user, prefix=""):
    """Prefer an explicit order lead; otherwise inherit the opportunity lead."""
    return (
        lead_ownership_q(user, f"{prefix}lead__")
        | (
            Q(**{f"{prefix}lead__isnull": True})
            & lead_ownership_q(user, f"{prefix}opportunity__lead__")
        )
    )


def invoice_ownership_q(user, prefix=""):
    """Resolve one invoice owner using deterministic relationship precedence."""
    order = Q(**{f"{prefix}order__isnull": False}) & production_ownership_q(user, f"{prefix}order__")
    no_order = Q(**{f"{prefix}order__isnull": True})
    advanced = (
        no_order
        & Q(**{f"{prefix}costing_header__isnull": False})
        & lead_ownership_q(user, f"{prefix}costing_header__opportunity__lead__")
    )
    quick = (
        no_order
        & Q(**{f"{prefix}costing_header__isnull": True})
        & lead_ownership_q(user, f"{prefix}quick_costing__opportunity__lead__")
    )
    return order | advanced | quick


def _lead_for_record(record):
    if isinstance(record, Lead):
        return record
    if isinstance(record, Opportunity):
        return record.lead
    if isinstance(record, (QuickCosting, CostingHeader)):
        return record.opportunity.lead if record.opportunity_id else None
    if isinstance(record, ProductionOrder):
        if record.lead_id:
            return record.lead
        return record.opportunity.lead if record.opportunity_id else None
    if isinstance(record, Invoice):
        if record.order_id:
            return _lead_for_record(record.order)
        if record.costing_header_id:
            return _lead_for_record(record.costing_header)
        if record.quick_costing_id:
            return _lead_for_record(record.quick_costing)
        return None
    if isinstance(record, InvoicePayment):
        return _lead_for_record(record.invoice)
    if isinstance(record, Customer):
        leads = record.leads.filter(is_archived=False).select_related("assigned_to").order_by("-created_date", "-id")
        attributed = leads.filter(Q(assigned_to__isnull=False) | ~Q(owner="")).first()
        return attributed or leads.first()
    order = getattr(record, "order", None)
    opportunity = getattr(record, "opportunity", None)
    return _lead_for_record(order or opportunity) if (order or opportunity) else None


def _author_user_id(record):
    for field_name in ("created_by", "quoted_by"):
        user_id = getattr(record, f"{field_name}_id", None)
        if user_id is not None:
            return user_id
    return None


def _audit_author(record):
    module_by_model = {
        "Customer": "customers",
        "Lead": "leads",
        "Opportunity": "opportunities",
        "CostingHeader": "quotations",
        "QuickCosting": "quick_costing",
        "ProductionOrder": "production",
        "Invoice": "invoices",
        "Shipment": "shipments",
    }
    module = module_by_model.get(record.__class__.__name__)
    if not module or not getattr(record, "pk", None):
        return None
    audit = (
        CRMAuditLog.objects.filter(
            module=module,
            record_id=str(record.pk),
            action_type=CRMAuditLog.ACTION_CREATED,
            actor__isnull=False,
        )
        .select_related("actor", "actor__employee_profile")
        .order_by("created_at", "id")
        .first()
    )
    return audit.actor if audit else None


def _cached_identity(user, *, index=None):
    if user is None:
        return None
    if index is not None:
        return resolve_employee_identity(user_id=user.pk, assigned_user=user, index=index)
    profile = user._state.fields_cache.get("employee_profile")
    if profile is None:
        return None
    canonical_name = canonical_employee_name(
        profile_display_name=profile.display_name,
        profile_full_name=profile.full_name,
        user_full_name=user.get_full_name(),
        username=user.get_username(),
    )
    cache.set(f"crm-employee-display:{user.pk}", profile.display_name or "", 300)
    return {
        "profile_id": profile.pk,
        "user_id": user.pk,
        "employee_id": profile.employee_id or "",
        "canonical_name": canonical_name,
        "display_name": profile.display_name or "",
        "full_name": user.get_full_name() or "",
        "username": user.get_username(),
        "aliases": profile.aliases or [],
    }


def attribution_for(record, *, index=None, include_author=True):
    """Return separately labelled salesperson-of-record and record author."""
    lead = _lead_for_record(record)
    assigned_user = lead._state.fields_cache.get("assigned_to") if lead else None
    salesperson = _cached_identity(assigned_user, index=index)
    if salesperson is None and lead:
        lookup_index = index or get_employee_identity_index()
        salesperson = resolve_employee_identity(
            user_id=lead.assigned_to_id, owner_text=lead.owner, index=lookup_index
        )
    if salesperson is None:
        salesperson = resolve_employee_identity(index=index or {"by_user_id": {}, "by_profile_id": {}, "by_token": {}})
    author_id = _author_user_id(record) if include_author else None
    author_user = None
    for field_name in (("created_by", "quoted_by") if include_author else ()):
        if getattr(record, f"{field_name}_id", None):
            author_user = record._state.fields_cache.get(field_name)
            break
    if include_author and author_user is None and not author_id:
        author_user = _audit_author(record)
        author_id = author_user.pk if author_user else None
    author_identity = _cached_identity(author_user, index=index)
    if author_identity is None and author_id:
        lookup_index = index or get_employee_identity_index()
        author_identity = resolve_employee_identity(user_id=author_id, index=lookup_index)
    if author_identity is None:
        author_identity = {
            "profile_id": None,
            "user_id": None,
            "employee_id": "",
            "canonical_name": "Unavailable",
            "display_name": "Unavailable",
            "full_name": "",
            "username": "",
            "aliases": [],
        }
    return {
        "salesperson": salesperson,
        "author": author_identity,
        "lead_id": getattr(lead, "lead_id", "") if lead else "",
    }


def _quoted_values(user):
    quick_value = ExpressionWrapper(
        F("quantity") * F("selling_price_per_piece"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    quick = (
        QuickCosting.objects.filter(lead_ownership_q(user, "opportunity__lead__"), quotation_number__gt="")
        .exclude(status=QuickCosting.STATUS_REJECTED)
        .values("currency")
        .annotate(
            amount=Sum(quick_value),
            count=Count("id"),
            approved=Count("id", filter=Q(status=QuickCosting.STATUS_APPROVED)),
        )
    )
    advanced_unit = Coalesce("manual_fob_per_piece", "opportunity__costing_fob_per_piece", ZERO)
    advanced_value = ExpressionWrapper(
        advanced_unit * F("order_quantity"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    advanced = (
        CostingHeader.objects.filter(
            lead_ownership_q(user, "opportunity__lead__"),
            is_archived=False,
            quotation_number__gt="",
        )
        .exclude(quotation_status__in=(CostingHeader.QUOTATION_STATUS_REJECTED, CostingHeader.QUOTATION_STATUS_DECLINED))
        .values("currency")
        .annotate(
            amount=Sum(advanced_value),
            count=Count("id"),
            approved=Count("id", filter=Q(quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED)),
        )
    )
    quick = list(quick)
    advanced = list(advanced)
    quick_rows = _rows(quick)
    advanced_rows = _rows(advanced)
    combined = []
    for quick_row, advanced_row in zip(quick_rows, advanced_rows):
        combined.append({
            "currency": quick_row["currency"],
            "amount": quick_row["amount"] + advanced_row["amount"],
            "count": quick_row["count"] + advanced_row["count"],
        })
    approved_count = sum(int(row.get("approved") or 0) for row in quick + advanced)
    return quick_rows, advanced_rows, combined, approved_count


def _production_values(user):
    local_value = ExpressionWrapper(
        F("qty_total") * F("sewing_charge_per_piece_bdt"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    queryset = ProductionOrder.objects.filter(
        production_ownership_q(user),
        is_archived=False,
        status__in=ACTIVE_PRODUCTION_STATUSES,
    ).annotate(
        currency=Coalesce(
            models.Case(
                models.When(order_type="sewing_charge", factory_location="bd", then=models.Value("BDT")),
                default=F("approved_currency"),
                output_field=models.CharField(max_length=10),
            ),
            models.Value("CAD"),
        ),
        value=Coalesce(
            models.Case(
                models.When(order_type="sewing_charge", factory_location="bd", then=local_value),
                default=F("approved_total_value"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
            ZERO,
        ),
    )
    grouped = list(
        queryset.values("currency").annotate(
            amount=Sum("value"),
            count=Count("id"),
            available_count=Count(
                "id",
                filter=Q(approved_total_value__isnull=False)
                | Q(order_type="sewing_charge", factory_location="bd", sewing_charge_per_piece_bdt__isnull=False),
            ),
        )
    )
    rows = _rows(grouped)
    available = {(row.get("currency") or "").upper(): int(row.get("available_count") or 0) for row in grouped}
    for row in rows:
        row["available_count"] = available.get(row["currency"], 0)
        row["unavailable_count"] = row["count"] - row["available_count"]
    return rows


def build_sales_kpis(user):
    """Build the canonical KPI set in at most ten bounded database queries."""
    today = timezone.localdate()
    month_start = today.replace(day=1)
    leads = Lead.objects.filter(is_archived=False).filter(lead_ownership_q(user))
    opportunities = Opportunity.objects.filter(is_archived=False, lead__in=leads)
    pipeline = summarize_pipeline(opportunities)

    lead_rows = list(
        leads.select_related("customer").only(
            "id", "lead_status", "next_followup", "next_follow_up_date", "customer_id",
            "customer__is_active", "customer__is_archived",
        ).annotate(
            activity_follow_ups=Count("activities", filter=Q(activities__activity_type="follow_up_sent")),
            activity_calls=Count("activities", filter=Q(activities__activity_type="call_made")),
            activity_emails=Count("activities", filter=Q(activities__activity_type="cold_email_sent")),
            activity_meetings=Count("activities", filter=Q(activities__activity_type="meeting_booked")),
            activity_conversions=Count("activities", filter=Q(activities__activity_type="converted")),
        )
    )
    lead_ids = {lead.pk for lead in lead_rows}
    terminal_lead_statuses = {"Converted", "Closed", "Disqualified", "Lost", "Unqualified"}
    lead_counts = {
        "total": len(lead_rows),
        "open": sum(lead.lead_status not in terminal_lead_statuses for lead in lead_rows),
        "converted": sum(lead.lead_status == "Converted" for lead in lead_rows),
        "lost": sum(lead.lead_status in {"Lost", "Unqualified"} for lead in lead_rows),
        "due_today": sum(lead.next_followup == today or lead.next_follow_up_date == today for lead in lead_rows),
        "overdue": sum(
            bool(
                (lead.next_followup and lead.next_followup < today)
                or (lead.next_follow_up_date and lead.next_follow_up_date < today)
            )
            for lead in lead_rows
        ),
    }

    opportunity_rows = list(
        with_pipeline_value(opportunities).values(
            "id", "stage", "is_open", "created_date", "updated_at", "closed_won_at",
            "customer_id", "pipeline_currency", "pipeline_value",
        )
    )
    won_totals = _empty_rows()
    monthly_won_totals = _empty_rows()
    lost_month_totals = _empty_rows()
    opportunity_counts = {"open": pipeline["count"], "won": 0, "lost": 0}
    sales_cycles = []
    won_customer_ids = set()
    pipeline_currency_counts = defaultdict(int)
    for opportunity in opportunity_rows:
        currency = (opportunity["pipeline_currency"] or "CAD").upper()
        value = opportunity["pipeline_value"] or ZERO
        if opportunity["is_open"] and opportunity["stage"] not in CLOSED_PIPELINE_STAGES:
            pipeline_currency_counts[currency] += 1
        if opportunity["stage"] == "Closed Won":
            opportunity_counts["won"] += 1
            won_customer_ids.add(opportunity["customer_id"])
            if currency in won_totals:
                won_totals[currency]["amount"] += value
                won_totals[currency]["count"] += 1
            closed_at = opportunity["closed_won_at"]
            won_date = closed_at.date() if closed_at else opportunity["created_date"]
            if month_start <= won_date <= today:
                monthly_won_totals[currency]["amount"] += value
                monthly_won_totals[currency]["count"] += 1
            if closed_at:
                sales_cycles.append(max((closed_at.date() - opportunity["created_date"]).days, 0))
        elif opportunity["stage"] == "Closed Lost":
            opportunity_counts["lost"] += 1
            if month_start <= opportunity["updated_at"].date() <= today and currency in lost_month_totals:
                lost_month_totals[currency]["amount"] += value
                lost_month_totals[currency]["count"] += 1

    closed_won_rows = [{"currency": currency, **won_totals[currency]} for currency in CURRENCIES]
    monthly_won_rows = [{"currency": currency, **monthly_won_totals[currency]} for currency in CURRENCIES]
    lost_month_rows = [{"currency": currency, **lost_month_totals[currency]} for currency in CURRENCIES]
    pipeline_amounts = {row["currency"]: row["amount"] for row in pipeline["rows"]}
    pipeline_rows = [
        {
            "currency": currency,
            "amount": pipeline_amounts.get(currency, ZERO),
            "count": pipeline_currency_counts[currency],
        }
        for currency in CURRENCIES
    ]
    quick_quotes, advanced_quotes, combined_quotes, approved_quote_count = _quoted_values(user)

    invoices = Invoice.objects.filter(
        invoice_ownership_q(user), is_archived=False, status__in=ISSUED_INVOICE_STATUSES
    ).distinct().prefetch_related("payments", "sales_commissions")
    invoice_totals = _empty_rows()
    payment_totals = _empty_rows()
    commission_totals = _empty_rows()
    commission_eligible_totals = _empty_rows()
    invoice_customer_counts = defaultdict(int)
    for invoice in invoices:
        currency = (invoice.currency or "").upper()
        if currency in invoice_totals:
            invoice_totals[currency]["amount"] += invoice.total_amount or ZERO
            invoice_totals[currency]["count"] += 1
        if invoice.customer_id:
            invoice_customer_counts[invoice.customer_id] += 1
        for payment in invoice.payments.all():
            payment_currency = (payment.currency or "").upper()
            if payment_currency in payment_totals:
                payment_totals[payment_currency]["amount"] += payment.amount or ZERO
                payment_totals[payment_currency]["count"] += 1
        for commission in invoice.sales_commissions.all():
            commission_currency = (commission.currency or "").upper()
            if commission_currency in commission_totals:
                commission_totals[commission_currency]["amount"] += commission.commission_amount or ZERO
                commission_totals[commission_currency]["count"] += 1
                commission_eligible_totals[commission_currency]["amount"] += commission.eligible_amount or ZERO
                commission_eligible_totals[commission_currency]["count"] += 1
    invoice_rows = [{"currency": currency, **invoice_totals[currency]} for currency in CURRENCIES]
    payment_rows = [{"currency": currency, **payment_totals[currency]} for currency in CURRENCIES]
    production_rows = _production_values(user)

    active_customer_ids = {
        lead.customer_id for lead in lead_rows
        if lead.customer_id and lead.customer and lead.customer.is_active and not lead.customer.is_archived
    }
    customer_counts = {
        "active": len(active_customer_ids),
        "won": len(won_customer_ids - {None}),
        "repeat": sum(count >= 2 for count in invoice_customer_counts.values()),
    }
    activity_counts = {
        "leads": lead_counts["total"],
        "follow_ups": sum(lead.activity_follow_ups for lead in lead_rows),
        "calls": sum(lead.activity_calls for lead in lead_rows),
        "emails": sum(lead.activity_emails for lead in lead_rows),
        "meetings": sum(lead.activity_meetings for lead in lead_rows),
        "conversions": sum(lead.activity_conversions for lead in lead_rows),
    }
    completed = opportunity_counts["won"] + opportunity_counts["lost"]
    closing_ratio = (
        (Decimal(opportunity_counts["won"]) / Decimal(completed) * Decimal("100")).quantize(Decimal("0.01"))
        if completed else ZERO
    )
    average_cycle = (
        (Decimal(sum(sales_cycles)) / Decimal(len(sales_cycles))).quantize(Decimal("0.1"))
        if sales_cycles else ZERO
    )
    commission_rows = [{"currency": currency, **commission_totals[currency]} for currency in CURRENCIES]
    commission_eligible_rows = [{"currency": currency, **commission_eligible_totals[currency]} for currency in CURRENCIES]
    metrics = {
        "lead_counts": lead_counts,
        "opportunity_counts": opportunity_counts,
        "pipeline_value": pipeline_rows,
        "pipeline_count": pipeline["count"],
        "closed_won_value": closed_won_rows,
        "closed_won_count": opportunity_counts["won"],
        "monthly_closed_won_value": monthly_won_rows,
        "lost_this_month": lost_month_rows,
        "won_this_month_count": sum(row["count"] for row in monthly_won_rows),
        "lost_this_month_count": sum(row["count"] for row in lost_month_rows),
        "closing_ratio": closing_ratio,
        "average_sales_cycle_days": average_cycle,
        "quick_quoted_value": quick_quotes,
        "advanced_quoted_value": advanced_quotes,
        "quoted_value": combined_quotes,
        "invoice_values": invoice_rows,
        "collected_values": payment_rows,
        "production_values": production_rows,
        "customer_counts": customer_counts,
        "activity_counts": activity_counts,
        "commission_values": commission_rows,
        "commission_eligible_values": commission_eligible_rows,
        "production_counts": {
            "total": sum(row["count"] for row in production_rows),
            "month": 0,
        },
        "quotation_counts": {
            "open": sum(row["count"] for row in combined_quotes),
            "approved": approved_quote_count,
        },
    }
    # Compatibility names are defined here so every dashboard consumes the
    # same values without recomputing them in a view or adapter.
    metrics.update(
        {
            "sales_revenue": metrics["closed_won_value"],
            "monthly_sales_revenue": metrics["monthly_closed_won_value"],
            "average_deal_value": metrics["closed_won_value"],
            "paid_invoice_values": metrics["collected_values"],
            "paid_invoice_count": sum(row["count"] for row in metrics["collected_values"]),
        }
    )
    return metrics


def build_employee_sales_statistics(user):
    """Canonical compact employee statistics derived from the KPI service."""
    metrics = build_sales_kpis(user)
    last_activity = LeadActivity.objects.filter(
        Q(user=user) | Q(lead__assigned_to=user)
    ).aggregate(last=Max("created_at"))["last"]
    return {
        "leads": metrics["lead_counts"]["total"],
        "open_opportunities": metrics["opportunity_counts"]["open"],
        "won_opportunities": metrics["opportunity_counts"]["won"],
        "production_orders": metrics["production_counts"]["total"],
        "invoices": sum(row["count"] for row in metrics["invoice_values"]),
        "revenue": metrics["sales_revenue"],
        "closing_ratio": metrics["closing_ratio"],
        "average_deal_size": metrics["average_deal_value"],
        "last_activity": last_activity,
    }


def build_team_sales_kpis():
    """Canonical, bounded-query aggregation for Team Performance."""
    sales_profiles = list(
        EmployeeProfile.objects.filter(user__groups__name="Sales", is_archived=False)
        .select_related("user", "manager", "manager__employee_profile")
        .distinct()
        .order_by("display_name", "user__username")
    )
    user_ids = [profile.user_id for profile in sales_profiles]
    profile_by_user = {profile.user_id: profile for profile in sales_profiles}
    identity_index = build_employee_identity_index(sales_profiles)
    rows = {
        user_id: {
            "profile": profile,
            "name": profile.public_name,
            "opportunities": 0,
            "won": 0,
            "lost": 0,
            "closing_ratio": ZERO,
            "overdue_followups": 0,
            "completed_followups": 0,
            "revenue": {currency: ZERO for currency in CURRENCIES},
        }
        for user_id, profile in profile_by_user.items()
    }
    if user_ids:
        known_sales_owner = known_employee_owner_q(index=identity_index)
        lead_scope = Q(assigned_to_id__in=user_ids)
        if known_sales_owner:
            lead_scope |= Q(assigned_to__isnull=True) & known_sales_owner
        for row in (
            Lead.objects.filter(is_archived=False)
            .filter(lead_scope)
            .values("assigned_to_id", "owner")
            .annotate(
                overdue=Count(
                    "id",
                    filter=Q(next_followup__lt=timezone.localdate())
                    | Q(next_follow_up_date__lt=timezone.localdate()),
                    distinct=True,
                ),
            )
        ):
            identity = resolve_employee_identity(
                user_id=row["assigned_to_id"], owner_text=row["owner"], index=identity_index
            )
            if identity["user_id"] in rows:
                rows[identity["user_id"]]["overdue_followups"] += int(row["overdue"] or 0)

        opportunity_scope = Q(lead__assigned_to_id__in=user_ids)
        known_opportunity_owner = known_employee_owner_q(prefix="lead__", index=identity_index)
        if known_opportunity_owner:
            opportunity_scope |= Q(lead__assigned_to__isnull=True) & known_opportunity_owner
        opportunity_rows = list(
            with_pipeline_value(Opportunity.objects.filter(is_archived=False).filter(opportunity_scope))
            .values("lead__assigned_to_id", "lead__owner", "pipeline_currency")
            .annotate(
                total=Count("id"),
                won=Count("id", filter=Q(stage="Closed Won")),
                lost=Count("id", filter=Q(stage="Closed Lost")),
                revenue=Sum("pipeline_value", filter=Q(stage="Closed Won")),
            )
        )
        for row in opportunity_rows:
            identity = resolve_employee_identity(
                user_id=row["lead__assigned_to_id"], owner_text=row["lead__owner"], index=identity_index
            )
            if identity["user_id"] not in rows:
                continue
            item = rows[identity["user_id"]]
            item["opportunities"] += int(row["total"] or 0)
            item["won"] += int(row["won"] or 0)
            item["lost"] += int(row["lost"] or 0)
            currency = (row["pipeline_currency"] or "").upper()
            if currency in CURRENCIES:
                item["revenue"][currency] += row["revenue"] or ZERO

        activity_scope = Q(user_id__in=user_ids) | Q(lead__assigned_to_id__in=user_ids)
        known_activity_owner = known_employee_owner_q(prefix="lead__", index=identity_index)
        if known_activity_owner:
            activity_scope |= Q(lead__assigned_to__isnull=True) & known_activity_owner
        for row in (
            LeadActivity.objects.filter(activity_type="follow_up_sent")
            .filter(activity_scope)
            .values("user_id", "lead__assigned_to_id", "lead__owner")
            .annotate(total=Count("id"))
        ):
            identity = resolve_employee_identity(
                user_id=row["user_id"] or row["lead__assigned_to_id"],
                owner_text=row["lead__owner"],
                index=identity_index,
            )
            if identity["user_id"] in rows:
                rows[identity["user_id"]]["completed_followups"] += int(row["total"] or 0)

    team_rows = list(rows.values())
    for row in team_rows:
        completed = row["won"] + row["lost"]
        row["closing_ratio"] = (
            (Decimal(row["won"]) / Decimal(completed) * Decimal("100")).quantize(Decimal("0.01"))
            if completed else ZERO
        )
        row["revenue_rows"] = [
            {"currency": currency, "amount": row["revenue"][currency]} for currency in CURRENCIES
        ]

    def leader(key):
        winner = max(team_rows, key=lambda item: (item[key], item["name"]), default=None)
        return winner if winner and winner[key] else None

    revenue_leaders = []
    for currency in CURRENCIES:
        winner = max(
            team_rows,
            key=lambda item: (item["revenue"][currency], item["name"]),
            default=None,
        )
        if winner and not winner["revenue"][currency]:
            winner = None
        revenue_leaders.append(
            {
                "currency": currency,
                "amount": winner["revenue"][currency] if winner else ZERO,
                "name": winner["name"] if winner else "No data",
            }
        )

    status_profiles = list(
        EmployeeProfile.objects.filter(
            is_archived=False,
            status__in=(EmployeeProfile.STATUS_ON_LEAVE, EmployeeProfile.STATUS_SUSPENDED),
        )
        .select_related("user")
        .order_by("display_name", "user__username")
    )
    newest_employees = sorted(sales_profiles, key=lambda profile: profile.user.date_joined, reverse=True)[:5]
    return {
        "team_rows": sorted(team_rows, key=lambda row: (-row["won"], row["name"])),
        "top_salesperson": leader("won"),
        "highest_closing_ratio": leader("closing_ratio"),
        "most_opportunities": leader("opportunities"),
        "most_followups_completed": leader("completed_followups"),
        "most_overdue_followups": leader("overdue_followups"),
        "revenue_leaders": revenue_leaders,
        "newest_employees": newest_employees,
        "employees_on_leave": [p for p in status_profiles if p.status == EmployeeProfile.STATUS_ON_LEAVE],
        "suspended_employees": [p for p in status_profiles if p.status == EmployeeProfile.STATUS_SUSPENDED],
    }


def _currency_amount_rows(amounts):
    return [{"currency": currency, "amount": amounts.get(currency, ZERO)} for currency in CURRENCIES]


def _rank_invoice_group(grouped, limit=5):
    ranked = sorted(grouped.items(), key=lambda item: (-item[1]["count"], item[0].lower()))[:limit]
    return [
        {
            "label": label,
            "count": values["count"],
            "amounts": _currency_amount_rows(values["amounts"]),
        }
        for label, values in ranked
    ]


def _ceo_invoice_kpis(queryset, *, today, month_start):
    """Aggregate all CEO invoice-sales widgets in one bounded invoice query."""
    today_amounts = defaultdict(Decimal)
    monthly_amounts = defaultdict(Decimal)
    customer_groups = defaultdict(lambda: {"count": 0, "amounts": defaultdict(Decimal)})
    salesperson_groups = defaultdict(lambda: {"count": 0, "amounts": defaultdict(Decimal)})
    identity_index = get_employee_identity_index()
    invoices = queryset.filter(issue_date__range=(month_start, today)).select_related(
        "customer",
        "order__lead__assigned_to",
        "order__opportunity__lead__assigned_to",
        "costing_header__opportunity__lead__assigned_to",
        "quick_costing__opportunity__lead__assigned_to",
    )
    for invoice in invoices:
        currency = (invoice.currency or "").upper()
        if currency not in CURRENCIES:
            continue
        amount = invoice.total_amount or ZERO
        monthly_amounts[currency] += amount
        if invoice.issue_date == today:
            today_amounts[currency] += amount

        customer = invoice.customer
        customer_label = (
            (customer.account_brand or customer.contact_name) if customer else None
        ) or "No customer"
        customer_groups[customer_label]["count"] += 1
        customer_groups[customer_label]["amounts"][currency] += amount

        if invoice.status in ISSUED_INVOICE_STATUSES:
            identity = attribution_for(invoice, index=identity_index, include_author=False)["salesperson"]
            if identity["user_id"] is not None or identity["canonical_name"] != "Unassigned":
                salesperson_label = identity["canonical_name"]
                salesperson_groups[salesperson_label]["count"] += 1
                salesperson_groups[salesperson_label]["amounts"][currency] += amount
    return {
        "today_sales": _currency_amount_rows(today_amounts),
        "monthly_sales": _currency_amount_rows(monthly_amounts),
        "top_customers": _rank_invoice_group(customer_groups),
        "top_salespeople": _rank_invoice_group(salesperson_groups),
    }


def build_ceo_sales_kpis(today=None):
    """All sales KPIs consumed by the CEO dashboard, from canonical sources."""
    today = today or timezone.localdate()
    month_start = today.replace(day=1)
    live_invoices = Invoice.objects.filter(is_archived=False).exclude(status="cancelled")
    invoice_kpis = _ceo_invoice_kpis(live_invoices, today=today, month_start=month_start)
    open_pipeline = summarize_pipeline(Opportunity.objects.all())
    return {
        **invoice_kpis,
        "open_pipeline_count": open_pipeline["count"],
        "open_pipeline_rows": open_pipeline["rows"],
    }
