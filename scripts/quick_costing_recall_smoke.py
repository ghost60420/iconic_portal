from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crm.models import Invoice, Lead, Opportunity, ProductionOrder, QuickCosting


PREFIX = "SMOKE0184"


class RollbackSmoke(Exception):
    pass


def assert_ok(condition, message):
    if not condition:
        raise AssertionError(message)


def cleanup_prefix():
    quick_ids = list(
        QuickCosting.objects.filter(project_name__startswith=PREFIX).values_list("id", flat=True)
    )
    opportunity_ids = list(
        Opportunity.objects.filter(lead__account_brand__startswith=PREFIX).values_list("id", flat=True)
    )
    lead_ids = list(Lead.objects.filter(account_brand__startswith=PREFIX).values_list("id", flat=True))

    Invoice.objects.filter(quick_costing_id__in=quick_ids).delete()
    ProductionOrder.objects.filter(source_quick_costing_id__in=quick_ids).delete()
    QuickCosting.objects.filter(id__in=quick_ids).delete()
    Opportunity.objects.filter(id__in=opportunity_ids).delete()
    Lead.objects.filter(id__in=lead_ids).delete()


def prefix_counts():
    return {
        "leads": Lead.objects.filter(account_brand__startswith=PREFIX).count(),
        "opportunities": Opportunity.objects.filter(lead__account_brand__startswith=PREFIX).count(),
        "quick_costings": QuickCosting.objects.filter(project_name__startswith=PREFIX).count(),
        "invoices": Invoice.objects.filter(quick_costing__project_name__startswith=PREFIX).count(),
        "production_orders": ProductionOrder.objects.filter(
            source_quick_costing__project_name__startswith=PREFIX
        ).count(),
    }


def make_admin():
    user_model = get_user_model()
    user, _created = user_model.objects.get_or_create(
        username=f"{PREFIX.lower()}-admin",
        defaults={
            "email": f"{PREFIX.lower()}-admin@example.com",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    changed_fields = []
    if not user.is_staff:
        user.is_staff = True
        changed_fields.append("is_staff")
    if not user.is_superuser:
        user.is_superuser = True
        changed_fields.append("is_superuser")
    if changed_fields:
        user.save(update_fields=changed_fields)
    return user


def make_opportunity():
    lead = Lead.objects.create(
        account_brand=f"{PREFIX} Brand",
        contact_name="Recall Smoke Buyer",
        email="smoke0184@example.com",
        product_category="Hoodie",
        primary_product_type="Streetwear",
        order_quantity="12",
    )
    return Opportunity.objects.create(
        lead=lead,
        product_category="Hoodie",
        product_type="Streetwear",
        moq_units=12,
    )


def make_quick_costing(user):
    quick = QuickCosting.objects.create(
        opportunity=make_opportunity(),
        buyer_name="Recall Smoke Buyer",
        project_name=f"{PREFIX} Quick Costing",
        product_type="Streetwear",
        pricing_type=QuickCosting.PRICING_CMT,
        quantity=12,
        currency="BDT",
        sewing_charge_per_piece_bdt=Decimal("150.00"),
        sewing_cost_per_piece_bdt=Decimal("90.00"),
        extra_local_cost_bdt=Decimal("100.00"),
        selling_price_per_piece=Decimal("150.00"),
        material_cost=Decimal("0.00"),
        production_cost=Decimal("1080.00"),
        other_expenses=Decimal("100.00"),
        shipping_cost=Decimal("0.00"),
        commission_type=QuickCosting.COMMISSION_NONE,
        commission_value=Decimal("0.00"),
        created_by=user,
        approval_submitted_by=user,
        approval_submitted_at=timezone.now(),
        status=QuickCosting.STATUS_SUBMITTED,
    )
    return quick


def run_smoke():
    cleanup_prefix()
    assert_ok(all(value == 0 for value in prefix_counts().values()), "prefix cleanup failed before smoke")

    admin = make_admin()
    client = Client(HTTP_HOST="127.0.0.1")
    client.force_login(admin)

    v1 = make_quick_costing(admin)

    response = client.post(reverse("quick_costing_approve", args=[v1.pk]))
    v1.refresh_from_db()
    assert_ok(response.status_code == 302, f"approve costing returned {response.status_code}")
    assert_ok(v1.status == QuickCosting.STATUS_APPROVED, f"approve costing status={v1.status}")

    response = client.post(reverse("quick_costing_convert_to_quotation", args=[v1.pk]))
    v1.refresh_from_db()
    assert_ok(response.status_code == 302, f"create quotation returned {response.status_code}")
    assert_ok(v1.quotation_number, "quotation number was not created")

    response = client.post(
        reverse("quick_costing_request_recall", args=[v1.pk]),
        data={"reason": "Smoke test revision required."},
    )
    v1.refresh_from_db()
    assert_ok(response.status_code == 302, f"request recall returned {response.status_code}")
    assert_ok(v1.status == QuickCosting.STATUS_RECALL_REQUESTED, f"request recall status={v1.status}")

    response = client.post(reverse("quick_costing_approve_recall", args=[v1.pk]))
    v1.refresh_from_db()
    assert_ok(response.status_code == 302, f"approve recall returned {response.status_code}")
    assert_ok(v1.status == QuickCosting.STATUS_RECALLED, f"approve recall status={v1.status}")
    assert_ok(v1.quotation_revision_required, "quotation was not marked revision required")

    response = client.post(reverse("quick_costing_create_revision_copy", args=[v1.pk]))
    assert_ok(response.status_code == 302, f"create revision returned {response.status_code}")
    v2 = QuickCosting.objects.get(previous_revision=v1)
    v1.refresh_from_db()
    assert_ok(v2.status == QuickCosting.STATUS_DRAFT, f"V2 draft status={v2.status}")
    assert_ok(v2.revision_number == 2, f"V2 revision number={v2.revision_number}")
    assert_ok(v1.status == QuickCosting.STATUS_RECALLED, f"V1 changed before V2 approval: {v1.status}")

    v2.status = QuickCosting.STATUS_SUBMITTED
    v2.approval_submitted_by = admin
    v2.approval_submitted_at = timezone.now()
    v2.save(update_fields=["status", "approval_submitted_by", "approval_submitted_at", "updated_at"])
    response = client.post(reverse("quick_costing_approve", args=[v2.pk]))
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert_ok(response.status_code == 302, f"approve V2 returned {response.status_code}")
    assert_ok(v2.status == QuickCosting.STATUS_APPROVED, f"V2 status={v2.status}")
    assert_ok(v1.status == QuickCosting.STATUS_SUPERSEDED, f"V1 status={v1.status}")
    assert_ok(v1.superseded_by_id == v2.pk, "V1 superseded_by does not point to V2")

    invoice_response = client.post(reverse("quick_costing_convert_to_invoice", args=[v1.pk]))
    production_response = client.post(reverse("quick_costing_convert_to_production", args=[v1.pk]))
    assert_ok(invoice_response.status_code == 302, f"old invoice gate returned {invoice_response.status_code}")
    assert_ok(production_response.status_code == 302, f"old production gate returned {production_response.status_code}")
    assert_ok(
        invoice_response["Location"] == reverse("quick_costing_detail", args=[v2.pk]),
        f"old invoice did not redirect to V2: {invoice_response['Location']}",
    )
    assert_ok(
        production_response["Location"] == reverse("quick_costing_detail", args=[v2.pk]),
        f"old production did not redirect to V2: {production_response['Location']}",
    )
    assert_ok(not Invoice.objects.filter(quick_costing=v1).exists(), "old V1 created an invoice")
    assert_ok(
        not ProductionOrder.objects.filter(source_quick_costing=v1).exists(),
        "old V1 created a production order",
    )

    old_detail_response = client.get(reverse("quick_costing_detail", args=[v1.pk]))
    assert_ok(old_detail_response.status_code == 200, f"old detail returned {old_detail_response.status_code}")
    old_detail = old_detail_response.content.decode("utf-8", errors="ignore")
    assert_ok("This is not the active costing revision." in old_detail, "inactive revision banner missing")
    assert_ok("Open Latest Version" in old_detail, "open latest version link missing")

    dashboard_response = client.get(reverse("ceo_dashboard"))
    assert_ok(dashboard_response.status_code == 200, f"CEO dashboard returned {dashboard_response.status_code}")
    dashboard = dashboard_response.content.decode("utf-8", errors="ignore")
    assert_ok("Total Active Revisions" in dashboard, "CEO dashboard active revision metric missing")
    assert_ok("Superseded Revisions" in dashboard, "CEO dashboard superseded metric missing")
    assert_ok("Recalled Revisions" in dashboard, "CEO dashboard recalled metric missing")

    print("SMOKE_PASS approve costing")
    print("SMOKE_PASS request recall")
    print("SMOKE_PASS create V2")
    print("SMOKE_PASS approve V2")
    print("SMOKE_PASS V1 becomes superseded")
    print("SMOKE_PASS old revision cannot create invoice")
    print("SMOKE_PASS old revision cannot create production")
    print("SMOKE_PASS latest approved redirect/link")
    print("SMOKE_PASS CEO dashboard metrics")


try:
    with transaction.atomic():
        run_smoke()
        raise RollbackSmoke()
except RollbackSmoke:
    pass

cleanup_prefix()
remaining = prefix_counts()
print("PREFIX_COUNTS_AFTER=", remaining)
if any(remaining.values()):
    raise SystemExit(f"prefix records remain after smoke: {remaining}")

with connection.cursor() as cursor:
    cursor.execute("PRAGMA foreign_key_check")
    foreign_key_errors = cursor.fetchall()
print("FOREIGN_KEY_ERRORS_AFTER_SMOKE=", len(foreign_key_errors))
if foreign_key_errors:
    raise SystemExit(f"foreign key errors after smoke: {foreign_key_errors[:5]}")

print("QUICK_COSTING_RECALL_SMOKE_OK")
