from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum

from crm.models import Lead, Opportunity, Shipment, AccountingEntry


def run_health_scan():
    now = timezone.now()
    day_7 = now - timedelta(days=7)
    day_30 = now - timedelta(days=30)

    alerts = []

    # Leads stopped (only if Lead has created_at)
    if hasattr(Lead, "created_at"):
        leads_7 = Lead.objects.filter(created_at__gte=day_7).count()
        if leads_7 == 0:
            alerts.append({
                "severity": "warning",
                "source": "leads",
                "title": "No new leads in last 7 days",
                "details": "Check website form, campaigns, and lead capture.",
            })

    # Open opportunities high (only if Opportunity has status)
    if hasattr(Opportunity, "status"):
        open_opp = Opportunity.objects.exclude(status__in=["Closed won", "Closed lost"]).count()
        if open_opp >= 20:
            alerts.append({
                "severity": "warning",
                "source": "sales",
                "title": "High number of open opportunities",
                "details": f"There are {open_opp} open opportunities. Review follow ups.",
            })

    # Shipping delayed (only if Shipment has status)
    if hasattr(Shipment, "status"):
        delayed_ship = Shipment.objects.filter(status__icontains="delay").count()
        if delayed_ship > 0:
            alerts.append({
                "severity": "critical",
                "source": "shipping",
                "title": "Delayed shipments found",
                "details": f"{delayed_ship} shipments look delayed. Review Shipping.",
            })

    # Accounting no entries last 3 days (only if AccountingEntry has date)
    if hasattr(AccountingEntry, "date"):
        last_3 = now.date() - timedelta(days=3)
        recent_entries = AccountingEntry.objects.filter(date__gte=last_3).count()
        if recent_entries == 0:
            alerts.append({
                "severity": "warning",
                "source": "accounting",
                "title": "No accounting entries in last 3 days",
                "details": "Check BD daily sheet and CA transfers updates.",
            })

    # Optional net cash check (only if fields exist)
    if hasattr(AccountingEntry, "amount") and hasattr(AccountingEntry, "currency") and hasattr(AccountingEntry, "date"):
        net = AccountingEntry.objects.filter(
            currency="CAD",
            date__gte=day_30.date()
        ).aggregate(s=Sum("amount"))["s"] or 0

        if net < 0:
            alerts.append({
                "severity": "critical",
                "source": "accounting",
                "title": "Net cash negative in last 30 days (CAD)",
                "details": f"Net cash is {net}. Review cash outflows.",
            })

    return alerts