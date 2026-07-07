from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import Customer, ExchangeRate, Invoice, Lead, Opportunity, ProductionOrder, QuickCosting


class InvoiceFromOpportunityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="invoice-prefill-admin",
            email="invoice-prefill@example.com",
            password="pass",
        )
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            account_brand="Prefill Brand",
            contact_name="Prefill Buyer",
            email="buyer@example.com",
            phone="+1 604 555 0100",
            country="Bangladesh",
            market="BD",
        )
        self.lead = Lead.objects.create(
            customer=self.customer,
            lead_id="LEAD-PREFILL",
            account_brand="Prefill Brand",
            contact_name="Prefill Buyer",
            email="lead@example.com",
            phone="+880 1711 000000",
            market="BD",
            product_category="Hoodie",
        )

    def opportunity(self, **overrides):
        values = {
            "lead": self.lead,
            "customer": self.customer,
            "stage": "Proposal",
            "product_type": "Streetwear",
            "product_category": "Hoodie",
            "moq_units": 2,
            "order_currency": "BDT",
            "order_value": Decimal("250000.00"),
            "order_value_usd": Decimal("250000.00"),
            "fx_rate_bdt_per_usd": Decimal("85.00"),
        }
        values.update(overrides)
        return Opportunity.objects.create(**values)

    def test_opportunity_detail_has_create_invoice_link_with_opportunity_id(self):
        opportunity = self.opportunity(order_currency="CAD", order_value=Decimal("1200.00"), order_value_usd=Decimal("1200.00"))

        response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{reverse('invoice_add')}?opportunity_id={opportunity.pk}")
        self.assertContains(response, "Create Invoice")

    def test_invoice_add_prefills_customer_opportunity_lead_order_and_bdt_conversion(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85.00"))
        opportunity = self.opportunity()
        order = ProductionOrder.objects.create(
            opportunity=opportunity,
            customer=self.customer,
            lead=self.lead,
            title="Prefill Hoodie Production",
            order_code="P0260705511257ABCDEF",
            qty_total=2,
        )

        response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={opportunity.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice from Opportunity")
        self.assertContains(response, opportunity.opportunity_id)
        self.assertContains(response, "LEAD-PREFILL")
        self.assertContains(response, "Prefill Brand")
        self.assertContains(response, "Prefill Buyer")
        self.assertContains(response, "buyer@example.com")
        self.assertContains(response, "+1 604 555 0100")
        self.assertContains(response, "Streetwear")
        self.assertContains(response, "Hoodie")
        self.assertContains(response, order.purchase_order_number)
        self.assertContains(response, "৳250,000.00")
        self.assertContains(response, "CAD $2,941.18")
        self.assertContains(response, "৳125,000.00")
        self.assertContains(response, "CAD $1,470.59")
        self.assertContains(response, f'<option value="{self.customer.pk}" selected>', html=False)
        self.assertContains(response, f'<option value="{order.pk}" selected>', html=False)
        self.assertContains(response, 'value="BDT" selected', html=False)

    def test_no_production_order_message_and_post_links_invoice_to_opportunity(self):
        opportunity = self.opportunity()

        get_response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={opportunity.pk}")

        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "No production order linked yet. Invoice will be linked to this opportunity.")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        self.assertFalse(Invoice.objects.filter(opportunity=opportunity).exists())

        post_response = self.client.post(
            f"{reverse('invoice_add')}?opportunity_id={opportunity.pk}",
            {
                "source_opportunity_id": str(opportunity.pk),
                "order": "",
                "customer": str(self.customer.pk),
                "invoice_number": "",
                "issue_date": "2026-07-07",
                "due_date": "",
                "currency": "BDT",
                "invoice_market": "bangladesh",
                "invoice_type": "bulk",
                "deposit_percentage": "50.00",
                "subtotal": "250000.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "paid_amount": "0.00",
                "status": "draft",
                "notes": "Manual edited note",
            },
        )

        self.assertEqual(post_response.status_code, 302)
        invoice = Invoice.objects.get(opportunity=opportunity)
        self.assertEqual(invoice.customer, self.customer)
        self.assertIsNone(invoice.order)
        self.assertEqual(invoice.subtotal, Decimal("250000.00"))
        self.assertEqual(invoice.total_amount, Decimal("250000.00"))
        self.assertEqual(invoice.notes, "Manual edited note")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        opportunity.refresh_from_db()
        self.assertNotEqual(opportunity.stage, "Production")

    def test_existing_invoice_warning_does_not_create_duplicate_on_get(self):
        opportunity = self.opportunity()
        existing = Invoice.objects.create(
            opportunity=opportunity,
            customer=self.customer,
            invoice_number="INV-PREFILL-EXISTING",
            currency="BDT",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )

        response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={opportunity.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing invoice found for this opportunity")
        self.assertContains(response, existing.invoice_number)
        self.assertEqual(Invoice.objects.filter(opportunity=opportunity).count(), 1)

    def test_invoice_type_defaults_from_sample_sewing_and_bulk_context(self):
        sample_opp = self.opportunity(product_category="Sample")
        QuickCosting.objects.create(
            opportunity=sample_opp,
            account_brand="Prefill Brand",
            contact_name="Prefill Buyer",
            buyer_name="Prefill Buyer",
            project_name="Sample Hoodie",
            product_type="Streetwear",
            costing_purpose=QuickCosting.PURPOSE_SAMPLE,
            pricing_type=QuickCosting.PRICING_FULL_PACKAGE,
            quantity=2,
        )
        sewing_opp = self.opportunity(product_category="Sewing")
        QuickCosting.objects.create(
            opportunity=sewing_opp,
            account_brand="Prefill Brand",
            contact_name="Prefill Buyer",
            buyer_name="Prefill Buyer",
            project_name="CMT Hoodie",
            product_type="Streetwear",
            costing_purpose=QuickCosting.PURPOSE_BULK,
            pricing_type=QuickCosting.PRICING_CMT,
            quantity=2,
        )
        bulk_opp = self.opportunity(product_category="Bulk")

        sample_response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={sample_opp.pk}")
        sewing_response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={sewing_opp.pk}")
        bulk_response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={bulk_opp.pk}")

        self.assertContains(sample_response, "Sample")
        self.assertContains(sample_response, 'value="sample" selected', html=False)
        self.assertContains(sewing_response, "Sewing Charge")
        self.assertContains(sewing_response, 'value="sewing_charge" selected', html=False)
        self.assertContains(bulk_response, "Bulk Production")
        self.assertContains(bulk_response, 'value="bulk" selected', html=False)

    def test_cad_and_usd_prefill_existing_currency_behavior(self):
        cad_opp = self.opportunity(
            order_currency="CAD",
            order_value=Decimal("1200.00"),
            order_value_usd=Decimal("1200.00"),
            fx_rate_bdt_per_usd=Decimal("85.00"),
        )
        usd_opp = self.opportunity(
            order_currency="USD",
            order_value=Decimal("117000.00"),
            order_value_usd=Decimal("900.00"),
            fx_rate_bdt_per_usd=Decimal("130.00"),
        )

        cad_response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={cad_opp.pk}")
        usd_response = self.client.get(f"{reverse('invoice_add')}?opportunity_id={usd_opp.pk}")

        self.assertContains(cad_response, "CAD $1,200.00")
        self.assertContains(cad_response, 'value="CAD" selected', html=False)
        self.assertContains(usd_response, "USD $900.00")
        self.assertContains(usd_response, 'value="USD" selected', html=False)
