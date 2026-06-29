from collections import defaultdict
from decimal import Decimal

from django.db.models import Avg, Count, F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from crm.models import CostingHeader, EmployeeProfile, Invoice, Lead, LeadActivity, Opportunity, ProductionOrder


CURRENCIES = ("CAD", "USD", "BDT")
TERMINAL_LEAD_STATUSES = ("Converted", "Closed", "Disqualified", "Lost", "Unqualified")


def _ownership_q(user, prefix=""):
    names = {user.get_username(), user.get_full_name().strip()}
    names.discard("")
    query = Q(**{f"{prefix}assigned_to": user})
    for name in names:
        query |= Q(**{f"{prefix}owner__iexact": name})
    return query


def _currency_rows(values):
    mapped = {row["currency"]: row for row in values if row.get("currency") in CURRENCIES}
    return [
        {
            "currency": currency,
            "amount": mapped.get(currency, {}).get("amount") or Decimal("0"),
            "average": mapped.get(currency, {}).get("average") or Decimal("0"),
            "count": int(mapped.get(currency, {}).get("count") or 0),
        }
        for currency in CURRENCIES
    ]


def _currency_metric_rows(values, prefix):
    mapped = {row["currency"]: row for row in values if row.get("currency") in CURRENCIES}
    return [
        {
            "currency": currency,
            "amount": mapped.get(currency, {}).get(f"{prefix}_amount") or Decimal("0"),
            "average": mapped.get(currency, {}).get(f"{prefix}_average") or Decimal("0"),
            "count": int(mapped.get(currency, {}).get(f"{prefix}_count") or 0),
        }
        for currency in CURRENCIES
    ]


def build_salesperson_profile(user):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    owned_leads = Lead.objects.filter(is_archived=False).filter(_ownership_q(user))
    followup_due = Q(next_followup=today) | Q(next_follow_up_date=today)
    followup_overdue = Q(next_followup__lt=today) | Q(next_follow_up_date__lt=today)
    converted_lead = Q(lead_status="Converted") | (
        Q(opportunities__isnull=False)
        & ~Q(lead_status__in=("Lost", "Unqualified", "Disqualified"))
    )
    open_lead = ~Q(lead_status__in=TERMINAL_LEAD_STATUSES) & Q(opportunities__isnull=True)
    lead_counts = owned_leads.aggregate(
        total=Count("id", distinct=True),
        open=Count("id", filter=open_lead, distinct=True),
        converted=Count("id", filter=converted_lead, distinct=True),
        lost=Count("id", filter=Q(lead_status__in=("Lost", "Unqualified")), distinct=True),
        due_today=Count("id", filter=followup_due & open_lead, distinct=True),
        overdue=Count("id", filter=followup_overdue & open_lead, distinct=True),
    )

    opportunities = Opportunity.objects.filter(is_archived=False, lead__in=owned_leads)
    won_this_month_filter = Q(
        closed_won_at__date__gte=month_start,
        closed_won_at__date__lte=today,
    ) | Q(
        closed_won_at__isnull=True,
        created_date__gte=month_start,
        created_date__lte=today,
    )
    won_filter = Q(stage="Closed Won")
    lost_filter = Q(stage="Closed Lost")
    lost_month_filter = lost_filter & Q(updated_at__date__gte=month_start, updated_at__date__lte=today)
    pipeline_filter = Q(is_open=True) & ~Q(stage__in=("Closed Won", "Closed Lost"))
    opportunity_rows = list(
        opportunities.values(currency=F("order_currency")).annotate(
            won_amount=Sum("order_value", filter=won_filter),
            won_average=Avg("order_value", filter=won_filter),
            won_count=Count("id", filter=won_filter),
            monthly_amount=Sum("order_value", filter=won_filter & won_this_month_filter),
            monthly_average=Avg("order_value", filter=won_filter & won_this_month_filter),
            monthly_count=Count("id", filter=won_filter & won_this_month_filter),
            lost_month_amount=Sum("order_value", filter=lost_month_filter),
            lost_month_average=Avg("order_value", filter=lost_month_filter),
            lost_month_count=Count("id", filter=lost_month_filter),
            pipeline_amount=Sum("order_value", filter=pipeline_filter),
            pipeline_average=Avg("order_value", filter=pipeline_filter),
            pipeline_count=Count("id", filter=pipeline_filter),
            lost_count=Count("id", filter=lost_filter),
            open_count=Count("id", filter=pipeline_filter),
        )
    )
    won_values = _currency_metric_rows(opportunity_rows, "won")
    monthly_won_values = _currency_metric_rows(opportunity_rows, "monthly")
    lost_this_month = _currency_metric_rows(opportunity_rows, "lost_month")
    pipeline_values = _currency_metric_rows(opportunity_rows, "pipeline")
    opportunity_counts = {
        "open": sum(int(row.get("open_count") or 0) for row in opportunity_rows),
        "won": sum(int(row.get("won_count") or 0) for row in opportunity_rows),
        "lost": sum(int(row.get("lost_count") or 0) for row in opportunity_rows),
    }
    completed = opportunity_counts["won"] + opportunity_counts["lost"]
    closing_ratio = (
        (Decimal(opportunity_counts["won"]) / Decimal(completed) * Decimal("100")).quantize(Decimal("0.01"))
        if completed
        else Decimal("0")
    )
    sales_cycles = []
    for created_date, closed_won_at in opportunities.filter(
        stage="Closed Won",
        closed_won_at__isnull=False,
    ).values_list("created_date", "closed_won_at"):
        sales_cycles.append(max((closed_won_at.date() - created_date).days, 0))
    average_sales_cycle_days = (
        (Decimal(sum(sales_cycles)) / Decimal(len(sales_cycles))).quantize(Decimal("0.1"))
        if sales_cycles
        else Decimal("0")
    )
    quotations = CostingHeader.objects.filter(quoted_by=user).exclude(quotation_number="")
    quotation_counts = quotations.aggregate(
        open=Count(
            "id",
            filter=Q(quotation_status__in=(
                CostingHeader.QUOTATION_STATUS_DRAFT,
                CostingHeader.QUOTATION_STATUS_APPROVED,
                CostingHeader.QUOTATION_STATUS_SENT,
            )),
        ),
        approved=Count("id", filter=Q(quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED)),
    )

    production = ProductionOrder.objects.filter(is_archived=False).filter(
        Q(lead__in=owned_leads) | Q(opportunity__in=opportunities)
    ).distinct()
    production_counts = production.aggregate(
        total=Count("id"),
        month=Count("id", filter=Q(created_at__date__gte=month_start, created_at__date__lte=today)),
    )

    invoices = Invoice.objects.exclude(status="cancelled").filter(
        Q(order__lead__in=owned_leads)
        | Q(order__opportunity__in=opportunities)
        | Q(costing_header__opportunity__in=opportunities)
    ).distinct()
    invoice_rows = list(
        invoices.values("currency").annotate(
            invoiced=Sum("total_amount"),
            collected=Sum("paid_amount"),
            count=Count("id"),
        )
    )
    invoice_map = defaultdict(lambda: {"invoiced": Decimal("0"), "collected": Decimal("0"), "count": 0})
    for row in invoice_rows:
        currency = (row.get("currency") or "").upper()
        if currency in CURRENCIES:
            invoice_map[currency] = {
                "invoiced": row.get("invoiced") or Decimal("0"),
                "collected": row.get("collected") or Decimal("0"),
                "count": int(row.get("count") or 0),
            }
    invoice_values = [
        {"currency": currency, **invoice_map[currency]}
        for currency in CURRENCIES
    ]
    paid_invoice_values = _currency_rows(
        invoices.filter(status="paid")
        .values("currency")
        .annotate(amount=Sum("total_amount"), count=Count("id"))
    )

    return {
        "lead_counts": {key: int(value or 0) for key, value in lead_counts.items()},
        "opportunity_counts": opportunity_counts,
        "quotation_counts": {key: int(value or 0) for key, value in quotation_counts.items()},
        "production_counts": {key: int(value or 0) for key, value in production_counts.items()},
        "sales_revenue": won_values,
        "monthly_sales_revenue": monthly_won_values,
        "lost_this_month": lost_this_month,
        "pipeline_value": pipeline_values,
        "average_deal_value": won_values,
        "average_sales_cycle_days": average_sales_cycle_days,
        "invoice_values": invoice_values,
        "paid_invoice_values": paid_invoice_values,
        "won_this_month_count": sum(row["count"] for row in monthly_won_values),
        "lost_this_month_count": sum(row["count"] for row in lost_this_month),
        "paid_invoice_count": sum(row["count"] for row in paid_invoice_values),
        "closing_ratio": closing_ratio,
    }


def build_employee_sales_statistics(user):
    metrics = build_salesperson_profile(user)
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


def build_team_performance():
    sales_profiles = list(
        EmployeeProfile.objects.filter(user__groups__name="Sales")
        .select_related("user", "manager", "manager__employee_profile")
        .distinct()
        .order_by("display_name", "user__username")
    )
    user_ids = [profile.user_id for profile in sales_profiles]
    profile_by_user = {profile.user_id: profile for profile in sales_profiles}
    rows = {
        user_id: {
            "profile": profile,
            "name": profile.public_name,
            "opportunities": 0,
            "won": 0,
            "lost": 0,
            "closing_ratio": Decimal("0"),
            "overdue_followups": 0,
            "completed_followups": 0,
            "revenue": {currency: Decimal("0") for currency in CURRENCIES},
        }
        for user_id, profile in profile_by_user.items()
    }
    if user_ids:
        for row in (
            Lead.objects.filter(is_archived=False, assigned_to_id__in=user_ids)
            .values("assigned_to_id")
            .annotate(
                overdue=Count(
                    "id",
                    filter=Q(next_followup__lt=timezone.localdate()) | Q(next_follow_up_date__lt=timezone.localdate()),
                    distinct=True,
                ),
            )
        ):
            rows[row["assigned_to_id"]]["overdue_followups"] = int(row["overdue"] or 0)

        opportunity_rows = list(
            Opportunity.objects.filter(is_archived=False, lead__assigned_to_id__in=user_ids)
            .values("lead__assigned_to_id", "order_currency")
            .annotate(
                total=Count("id"),
                won=Count("id", filter=Q(stage="Closed Won")),
                lost=Count("id", filter=Q(stage="Closed Lost")),
                revenue=Sum("order_value", filter=Q(stage="Closed Won")),
            )
        )
        for row in opportunity_rows:
            item = rows[row["lead__assigned_to_id"]]
            item["opportunities"] += int(row["total"] or 0)
            item["won"] += int(row["won"] or 0)
            item["lost"] += int(row["lost"] or 0)
            currency = (row["order_currency"] or "").upper()
            if currency in CURRENCIES:
                item["revenue"][currency] += row["revenue"] or Decimal("0")

        for row in (
            LeadActivity.objects.filter(activity_type="follow_up_sent")
            .annotate(owner_id=Coalesce("user_id", "lead__assigned_to_id"))
            .filter(owner_id__in=user_ids)
            .values("owner_id")
            .annotate(total=Count("id"))
        ):
            rows[row["owner_id"]]["completed_followups"] = int(row["total"] or 0)

    team_rows = list(rows.values())
    for row in team_rows:
        completed = row["won"] + row["lost"]
        row["closing_ratio"] = (
            (Decimal(row["won"]) / Decimal(completed) * Decimal("100")).quantize(Decimal("0.01"))
            if completed
            else Decimal("0")
        )
        row["revenue_rows"] = [
            {"currency": currency, "amount": row["revenue"][currency]}
            for currency in CURRENCIES
        ]

    def leader(key):
        winner = max(team_rows, key=lambda row: (row[key], row["name"]), default=None)
        return winner if winner and winner[key] else None

    revenue_leaders = []
    for currency in CURRENCIES:
        winner = max(team_rows, key=lambda row: (row["revenue"][currency], row["name"]), default=None)
        if winner and not winner["revenue"][currency]:
            winner = None
        revenue_leaders.append(
            {
                "currency": currency,
                "amount": winner["revenue"][currency] if winner else Decimal("0"),
                "name": winner["name"] if winner else "No data",
            }
        )

    status_profiles = list(
        EmployeeProfile.objects.filter(
            status__in=(EmployeeProfile.STATUS_ON_LEAVE, EmployeeProfile.STATUS_SUSPENDED)
        ).select_related("user").order_by("display_name", "user__username")
    )
    newest_employees = sorted(
        sales_profiles,
        key=lambda profile: profile.user.date_joined,
        reverse=True,
    )[:5]
    return {
        "team_rows": sorted(team_rows, key=lambda row: (-row["won"], row["name"])),
        "top_salesperson": leader("won"),
        "highest_closing_ratio": leader("closing_ratio"),
        "most_opportunities": leader("opportunities"),
        "most_followups_completed": leader("completed_followups"),
        "most_overdue_followups": leader("overdue_followups"),
        "revenue_leaders": revenue_leaders,
        "newest_employees": newest_employees,
        "employees_on_leave": [profile for profile in status_profiles if profile.status == EmployeeProfile.STATUS_ON_LEAVE],
        "suspended_employees": [profile for profile in status_profiles if profile.status == EmployeeProfile.STATUS_SUSPENDED],
    }
