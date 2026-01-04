from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum

from .models import (
    Lead,
    Opportunity,
    Shipment,
    InventoryItem,
    AccountingEntry,
)

def build_health_report():
    """
    Read only health report.
    Returns:
      {
        "score": int,
        "checks": [ {name,status,score,detail}, ... ]
      }
    """
    now = timezone.now()
    checks = []

    def add_check(name, status, score, detail):
        checks.append({
            "name": name,
            "status": status,
            "score": score,
            "detail": detail,
        })

    # 1) Leads flow
    leads_7 = Lead.objects.filter(created_at__gte=now - timedelta(days=7)).count() if hasattr(Lead, "created_at") else Lead.objects.all().count()
    if leads_7 == 0:
        add_check("Lead intake", "warn", 70, "No leads found in the last 7 days. Check website form and ads.")
    else:
        add_check("Lead intake", "ok", 100, f"{leads_7} leads found in the last 7 days.")

    # 2) Opportunities
    opp_30 = Opportunity.objects.filter(created_at__gte=now - timedelta(days=30)).count() if hasattr(Opportunity, "created_at") else Opportunity.objects.all().count()
    if opp_30 == 0:
        add_check("Opportunities", "warn", 70, "No new opportunities in the last 30 days. Review follow ups.")
    else:
        add_check("Opportunities", "ok", 100, f"{opp_30} opportunities created in the last 30 days.")

    # 3) Shipping delays (best effort)
    ship_qs = Shipment.objects.all()
    delayed = 0
    if hasattr(Shipment, "is_delayed"):
        delayed = ship_qs.filter(is_delayed=True).count()
    elif hasattr(Shipment, "status"):
        delayed = ship_qs.filter(status__icontains="delay").count()

    if delayed > 0:
        add_check("Shipping delays", "warn", 75, f"{delayed} shipment(s) appear delayed. Review tracking.")
    else:
        add_check("Shipping delays", "ok", 100, "No delayed shipments detected.")

    # 4) Inventory low stock (best effort)
    inv_qs = InventoryItem.objects.all()
    low = 0
    if hasattr(InventoryItem, "reorder_level") and hasattr(InventoryItem, "quantity"):
        low = inv_qs.filter(quantity__lte=0).count()
        if low == 0:
            low = inv_qs.filter(quantity__lte=1).count()

    if low > 0:
        add_check("Inventory", "warn", 80, f"{low} item(s) are low or empty. Consider reorder.")
    else:
        add_check("Inventory", "ok", 100, "No low stock alerts detected.")

    # 5) Accounting sanity (last 30 days totals)
    acc_qs = AccountingEntry.objects.all()
    total_30 = None
    if hasattr(AccountingEntry, "date") and hasattr(AccountingEntry, "amount"):
        total_30 = acc_qs.filter(date__gte=(now.date() - timedelta(days=30))).aggregate(s=Sum("amount"))["s"] or 0

    if total_30 is None:
        add_check("Accounting", "ok", 100, "Accounting module reachable.")
    else:
        add_check("Accounting", "ok", 100, f"Last 30 days total amount: {total_30}")

    # Overall score
    if not checks:
        return {"score": 100, "checks": []}

    score = round(sum(c["score"] for c in checks) / len(checks))
    return {"score": score, "checks": checks}