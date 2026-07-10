from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse

from crm.models import Invoice, ProductionOrder, QuickCosting, Shipment


class RollbackPageHealth(Exception):
    pass


def assert_ok(condition, message):
    if not condition:
        raise AssertionError(message)


def run_page_health():
    user = get_user_model().objects.filter(is_superuser=True).order_by("id").first()
    assert_ok(user is not None, "No superuser available for page health checks.")
    client = Client(HTTP_HOST="127.0.0.1")
    client.force_login(user)

    quick = QuickCosting.objects.order_by("-id").first()
    assert_ok(quick is not None, "No Quick Costing records found.")

    quotation = (
        QuickCosting.objects.exclude(quotation_number="")
        .filter(quoted_at__isnull=False)
        .order_by("-id")
        .first()
    )
    assert_ok(quotation is not None, "No Quick Costing quotation records found.")

    invoice = Invoice.objects.order_by("-id").first()
    assert_ok(invoice is not None, "No invoice records found.")

    production = ProductionOrder.objects.order_by("-id").first()
    assert_ok(production is not None, "No production records found.")

    shipment = Shipment.objects.order_by("-id").first()
    assert_ok(shipment is not None, "No shipment records found.")

    checks = [
        ("Quick Costing detail", client.get(reverse("quick_costing_detail", args=[quick.pk]))),
        ("Quick Costing quotation", client.get(reverse("quick_costing_client_quotation", args=[quotation.pk]))),
        ("Invoice view", client.get(reverse("invoice_view", args=[invoice.pk]))),
        ("Production detail", client.get(reverse("production_detail", args=[production.pk]))),
        ("Shipment detail", client.get(reverse("shipment_detail", args=[shipment.pk]))),
        ("CEO dashboard", client.get(reverse("ceo_dashboard"))),
    ]
    for label, response in checks:
        print(f"PAGE_CHECK {label}: {response.status_code}")
        assert_ok(response.status_code == 200, f"{label} returned {response.status_code}.")


try:
    with transaction.atomic():
        run_page_health()
        raise RollbackPageHealth()
except RollbackPageHealth:
    pass

print("PAGE_HEALTH_OK")
