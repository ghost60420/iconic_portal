from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    CostingHeader,
    CostingLineItem,
    CRMAuditLog,
    Customer,
    Invoice,
    InvoicePayment,
    Lead,
    Opportunity,
    ProductionOrder,
)


class ApprovalGateRegressionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.sales = user_model.objects.create_user(username="gate-sales", password="pass")
        self.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.ceo = user_model.objects.create_user(username="gate-ceo", password="pass")
        self.ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        self.customer = Customer.objects.create(
            account_brand="Gate Client",
            contact_name="Gate Buyer",
            country="Canada",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="Gate Client",
            contact_name="Gate Buyer",
            assigned_to=self.sales,
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            stage="Proposal",
            product_type="Activewear",
            product_category="Hoodie",
            moq_units=100,
        )

    def costing(self, **overrides):
        values = {
            "opportunity": self.opportunity,
            "customer": self.customer,
            "style_name": "Gate Hoodie",
            "buyer": "Gate Buyer",
            "brand": "Gate Client",
            "product_type": "Activewear",
            "factory_location": "bd",
            "order_quantity": 100,
            "currency": "CAD",
            "manual_fob_per_piece": Decimal("25.00"),
            "status": "approved",
            "quotation_number": "QT-GATE-001",
            "quoted_by": self.sales,
            "quoted_at": timezone.now(),
        }
        values.update(overrides)
        costing = CostingHeader.objects.create(**values)
        CostingLineItem.objects.create(
            costing=costing,
            category="fabric",
            item_name="Main fabric",
            uom="piece",
            unit_price=Decimal("10.00"),
            consumption_value=Decimal("1.00"),
        )
        return costing

    def test_sales_user_cannot_move_opportunity_to_production_by_url(self):
        self.costing()
        self.client.force_login(self.sales)

        response = self.client.post(reverse("production_from_opportunity", args=[self.opportunity.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProductionOrder.objects.filter(opportunity=self.opportunity).exists())

    def test_unapproved_quotation_cannot_move_to_production(self):
        self.costing()
        self.client.force_login(self.ceo)

        response = self.client.post(reverse("production_from_opportunity", args=[self.opportunity.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductionOrder.objects.filter(opportunity=self.opportunity).exists())

    def test_ceo_can_move_ceo_approved_quotation_to_production(self):
        costing = self.costing(
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo,
            quotation_approved_at=timezone.now(),
        )
        self.client.force_login(self.ceo)

        response = self.client.post(reverse("production_from_opportunity", args=[self.opportunity.pk]))

        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(opportunity=self.opportunity)
        self.assertEqual(order.source_quotation, costing)


class OpportunityBDTCurrencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="bdt-currency-admin",
            email="bdt@example.com",
            password="pass",
        )
        self.customer = Customer.objects.create(account_brand="BD Client", contact_name="BD Buyer", country="Bangladesh")
        self.lead = Lead.objects.create(
            customer=self.customer,
            account_brand="BD Client",
            contact_name="BD Buyer",
            market="BD",
        )
        self.opportunity = Opportunity.objects.create(
            lead=self.lead,
            customer=self.customer,
            product_type="Other",
            product_category="Other",
            moq_units=2,
        )
        self.client.force_login(self.user)

    def test_opportunity_edit_supports_bdt_and_converts_to_cad(self):
        response = self.client.get(reverse("opportunity_edit", args=[self.opportunity.pk]))
        self.assertContains(response, '<option value="BDT"', html=False)

        response = self.client.post(
            reverse("opportunity_edit", args=[self.opportunity.pk]),
            {
                "product_type": "Other",
                "product_category": "Other",
                "order_currency": "BDT",
                "order_value_usd": "250000",
                "fx_rate_bdt_per_usd": "85",
                "moq_units": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.order_currency, "BDT")
        self.assertEqual(self.opportunity.order_value, Decimal("250000"))

        response = self.client.get(reverse("opportunity_edit", args=[self.opportunity.pk]))
        self.assertContains(response, "৳250,000.00")
        self.assertContains(response, "CAD $2,941.18")
        self.assertContains(response, "৳125,000.00")
        self.assertContains(response, "CAD $1,470.59")

    def test_missing_bdt_exchange_rate_shows_conversion_unavailable(self):
        self.client.post(
            reverse("opportunity_edit", args=[self.opportunity.pk]),
            {
                "product_type": "Other",
                "product_category": "Other",
                "order_currency": "BDT",
                "order_value_usd": "250000",
                "moq_units": "2",
            },
        )

        response = self.client.get(reverse("opportunity_edit", args=[self.opportunity.pk]))
        self.assertContains(response, "Conversion unavailable")


class InvoiceDeleteVoidControlTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.ceo = user_model.objects.create_user(username="invoice-ceo", password="pass")
        self.ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        self.sales = user_model.objects.create_user(username="invoice-sales", password="pass")
        self.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        self.accounts = user_model.objects.create_user(username="invoice-accounts", password="pass")
        self.accounts.groups.add(Group.objects.get_or_create(name="Accounts")[0])
        self.customer = Customer.objects.create(account_brand="Invoice Client", contact_name="Invoice Buyer")
        self.invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_number="INV-GATE-001",
            currency="CAD",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            paid_amount=Decimal("0.00"),
            status="sent",
        )

    def test_delete_button_visible_to_ceo_only_and_url_blocks_sales(self):
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("invoice_view", args=[self.invoice.pk]))
        self.assertContains(response, "Delete / Void")

        self.client.force_login(self.accounts)
        response = self.client.get(reverse("invoice_view", args=[self.invoice.pk]))
        self.assertNotContains(response, "Delete / Void")
        self.client.force_login(self.sales)
        response = self.client.get(reverse("invoice_delete_or_void", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 403)

    def test_reason_is_required_before_delete_or_void(self):
        self.client.force_login(self.ceo)

        response = self.client.post(reverse("invoice_delete_or_void", args=[self.invoice.pk]), {"action": "delete"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Invoice.objects.filter(pk=self.invoice.pk).exists())
        self.assertFalse(CRMAuditLog.objects.filter(module="invoice", record_id=str(self.invoice.pk)).exists())

    def test_unlinked_invoice_can_be_deleted_with_audit(self):
        invoice_pk = self.invoice.pk
        self.client.force_login(self.ceo)

        response = self.client.post(
            reverse("invoice_delete_or_void", args=[invoice_pk]),
            {"action": "delete", "reason": "Duplicate draft"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Invoice.objects.filter(pk=invoice_pk).exists())
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(invoice_pk),
                action_type=CRMAuditLog.ACTION_DELETED,
                new_value__icontains="Duplicate draft",
            ).exists()
        )

    def test_invoice_with_payment_or_accounting_link_cannot_hard_delete_but_can_void(self):
        entry = AccountingEntry.objects.create(
            date=timezone.localdate(),
            side=AccountingEntry.SIDE_CA,
            direction=AccountingEntry.DIR_IN,
            currency="CAD",
            amount_original=Decimal("25.00"),
            main_type="revenue",
            description="Invoice payment",
        )
        InvoicePayment.objects.create(
            invoice=self.invoice,
            accounting_entry=entry,
            amount=Decimal("25.00"),
            currency="CAD",
            side="CA",
        )
        self.client.force_login(self.ceo)

        response = self.client.post(
            reverse("invoice_delete_or_void", args=[self.invoice.pk]),
            {"action": "delete", "reason": "Has linked payment"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Invoice.objects.filter(pk=self.invoice.pk).exists())

        response = self.client.post(
            reverse("invoice_delete_or_void", args=[self.invoice.pk]),
            {"action": "void", "reason": "Client cancelled"},
        )

        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "cancelled")
        self.assertTrue(self.invoice.is_archived)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(self.invoice.pk),
                action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
                new_value__icontains="Client cancelled",
            ).exists()
        )
