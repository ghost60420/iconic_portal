from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    ActualCostEntry,
    CostingHeader,
    CostingLineItem,
    CRMAuditLog,
    Customer,
    ExchangeRate,
    Invoice,
    InvoicePayment,
    Lead,
    Opportunity,
    OrderLifecycle,
    ProductionOrder,
)
from crm.services.order_lifecycle import build_lifecycle_profit_breakdown
from crm.services.costing_workflow import create_invoice_from_costing


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
        invoice, _ = create_invoice_from_costing(costing, user=self.ceo)
        invoice.paid_amount = Decimal("750.00")
        invoice.status = "partial"
        invoice.deposit_percentage = Decimal("30.00")
        invoice.save(update_fields=["paid_amount", "status", "deposit_percentage", "updated_at"])
        self.client.force_login(self.ceo)

        response = self.client.post(reverse("production_from_opportunity", args=[self.opportunity.pk]))

        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(opportunity=self.opportunity)
        self.assertEqual(order.source_quotation, costing)

    def test_sales_sees_create_invoice_after_ceo_approved_quotation(self):
        costing = self.costing(
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo,
            quotation_approved_at=timezone.now(),
        )
        self.client.force_login(self.sales)

        response = self.client.get(reverse("cost_sheet_client_quotation", args=[costing.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Invoice")
        self.assertContains(response, f"{reverse('invoice_add')}?quotation_id={costing.pk}")

    def test_approved_quotation_invoice_prefill_uses_two_decimal_money_values(self):
        costing = self.costing(
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo,
            quotation_approved_at=timezone.now(),
        )
        self.client.force_login(self.sales)

        response = self.client.get(f"{reverse('invoice_add')}?quotation_id={costing.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="subtotal" value="2500.00"', html=False)
        self.assertNotContains(response, 'name="subtotal" value="2500.0000"', html=False)

    def test_sales_cannot_create_invoice_from_unapproved_quotation_url(self):
        costing = self.costing(quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT)
        self.client.force_login(self.sales)

        response = self.client.get(f"{reverse('invoice_add')}?quotation_id={costing.pk}")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Invoice.objects.filter(costing_header=costing).exists())

    def test_ceo_can_create_draft_invoice_from_approved_quotation_form(self):
        costing = self.costing(
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo,
            quotation_approved_at=timezone.now(),
        )
        self.client.force_login(self.ceo)

        response = self.client.post(
            f"{reverse('invoice_add')}?quotation_id={costing.pk}",
            {
                "source_quotation_id": str(costing.pk),
                "source_opportunity_id": str(self.opportunity.pk),
                "order": "",
                "customer": str(self.customer.pk),
                "invoice_number": "",
                "issue_date": "2026-07-08",
                "due_date": "",
                "currency": "CAD",
                "invoice_market": "north_america",
                "invoice_type": "bulk",
                "deposit_percentage": "50.00",
                "subtotal": "2500.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "paid_amount": "0.00",
                "status": "sent",
                "notes": "Draft from approved quotation",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(costing_header=costing)
        self.assertEqual(invoice.status, "draft")
        self.assertEqual(invoice.opportunity, self.opportunity)
        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(invoice.total_amount, Decimal("2500.00"))
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(invoice.pk),
                action_type=CRMAuditLog.ACTION_CREATED,
                field_name="status",
                previous_value="",
                new_value="draft",
                actor=self.ceo,
            ).exists()
        )

    def test_manual_invoice_starts_as_draft_even_if_sent_is_posted(self):
        self.client.force_login(self.ceo)

        response = self.client.post(
            reverse("invoice_add"),
            {
                "order": "",
                "customer": str(self.customer.pk),
                "invoice_number": "",
                "issue_date": "2026-07-08",
                "due_date": "",
                "currency": "CAD",
                "invoice_market": "north_america",
                "invoice_type": "bulk",
                "deposit_percentage": "50.00",
                "subtotal": "900.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "paid_amount": "0.00",
                "status": "sent",
                "notes": "Manual draft invoice",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(customer=self.customer, subtotal=Decimal("900.00"))
        self.assertEqual(invoice.status, "draft")
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(invoice.pk),
                action_type=CRMAuditLog.ACTION_CREATED,
                field_name="status",
                new_value="draft",
            ).exists()
        )

    def test_only_send_invoice_action_changes_draft_to_sent_and_preserves_financial_records(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_number="INV-SEND-DRAFT",
            currency="CAD",
            subtotal=Decimal("700.00"),
            total_amount=Decimal("700.00"),
            paid_amount=Decimal("0.00"),
            status="draft",
        )
        totals_before = (invoice.subtotal, invoice.total_amount, invoice.paid_amount)
        payment_count = InvoicePayment.objects.count()
        accounting_count = AccountingEntry.objects.count()
        self.client.force_login(self.ceo)

        response = self.client.post(reverse("invoice_approve", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "sent")
        self.assertEqual((invoice.subtotal, invoice.total_amount, invoice.paid_amount), totals_before)
        self.assertEqual(InvoicePayment.objects.count(), payment_count)
        self.assertEqual(AccountingEntry.objects.count(), accounting_count)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(invoice.pk),
                action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
                field_name="status",
                previous_value="draft",
                new_value="sent",
                actor=self.ceo,
            ).exists()
        )

    def test_sales_cannot_send_invoice_by_direct_url(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_number="INV-SALES-CANNOT-SEND",
            currency="CAD",
            subtotal=Decimal("700.00"),
            total_amount=Decimal("700.00"),
            paid_amount=Decimal("0.00"),
            status="draft",
        )
        self.client.force_login(self.sales)

        response = self.client.post(reverse("invoice_approve", args=[invoice.pk]))

        self.assertEqual(response.status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")

    def test_payment_action_logs_paid_status_change(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_number="INV-PAID-AUDIT",
            currency="CAD",
            invoice_region="CA",
            subtotal=Decimal("700.00"),
            total_amount=Decimal("700.00"),
            paid_amount=Decimal("0.00"),
            status="sent",
        )
        self.client.force_login(self.ceo)

        response = self.client.post(
            reverse("invoice_payment_add", args=[invoice.pk]),
            {
                "payment_date": "2026-07-08",
                "amount": "700.00",
                "currency": "CAD",
                "side": "CA",
                "payment_method": "bank",
                "rate_to_cad": "1",
                "rate_to_bdt": "85",
                "production_order": "",
                "notes": "Paid in full",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.paid_amount, Decimal("700.00"))
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="invoice",
                record_id=str(invoice.pk),
                action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
                field_name="status",
                previous_value="sent",
                new_value="paid",
                actor=self.ceo,
            ).exists()
        )

    def test_existing_quotation_invoice_warning_blocks_duplicate_post(self):
        costing = self.costing(
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_by=self.ceo,
            quotation_approved_at=timezone.now(),
        )
        existing = Invoice.objects.create(
            costing_header=costing,
            opportunity=self.opportunity,
            customer=self.customer,
            invoice_number="INV-EXISTING-QUOTE",
            currency="CAD",
            subtotal=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            status="draft",
        )
        self.client.force_login(self.sales)

        get_response = self.client.get(f"{reverse('invoice_add')}?quotation_id={costing.pk}")
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Existing invoice found for this quotation")
        self.assertContains(get_response, existing.invoice_number)

        post_response = self.client.post(
            f"{reverse('invoice_add')}?quotation_id={costing.pk}",
            {
                "source_quotation_id": str(costing.pk),
                "source_opportunity_id": str(self.opportunity.pk),
                "customer": str(self.customer.pk),
                "currency": "CAD",
                "invoice_market": "north_america",
                "invoice_type": "bulk",
                "subtotal": "2500.00",
                "shipping_amount": "0.00",
                "discount_amount": "0.00",
                "tax_amount": "0.00",
                "paid_amount": "0.00",
                "status": "draft",
            },
        )

        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(Invoice.objects.filter(costing_header=costing).count(), 1)


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


class CurrencyProfitDisplayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="currency-profit-admin",
            email="currency-profit@example.com",
            password="pass",
        )
        self.client.force_login(self.user)

    def _customer_profit(self, *, customer_name, currency, revenue, cost, profit):
        customer = Customer.objects.create(account_brand=customer_name, contact_name=f"{customer_name} Buyer")
        invoice = Invoice.objects.create(
            customer=customer,
            invoice_number=f"INV-{currency}-{customer.pk}",
            currency=currency,
            invoice_region="BD" if currency == "BDT" else "CA",
            subtotal=revenue,
            total_amount=revenue,
            paid_amount=Decimal("0.00"),
            status="sent",
        )
        OrderLifecycle.objects.create(
            customer=customer,
            invoice=invoice,
            status="invoice",
            estimated_revenue=revenue,
            estimated_cost=cost,
            estimated_profit=profit,
            estimated_margin=Decimal("50.00"),
        )
        return customer

    def test_most_profitable_customers_group_currency_and_show_bdt_cad_equivalent(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85.00"))
        self._customer_profit(
            customer_name="Customer A",
            currency="CAD",
            revenue=Decimal("300000.00"),
            cost=Decimal("59500.00"),
            profit=Decimal("240500.00"),
        )
        self._customer_profit(
            customer_name="Customer B",
            currency="USD",
            revenue=Decimal("10000.00"),
            cost=Decimal("2000.00"),
            profit=Decimal("8000.00"),
        )
        self._customer_profit(
            customer_name="Afasy Ltd.",
            currency="BDT",
            revenue=Decimal("120000.00"),
            cost=Decimal("30000.00"),
            profit=Decimal("90000.00"),
        )

        response = self.client.get(reverse("ceo_operations_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Most profitable customers")
        self.assertContains(response, "Customer A")
        self.assertContains(response, "CAD $240,500.00 profit")
        self.assertContains(response, "Customer B")
        self.assertContains(response, "USD $8,000.00 profit")
        self.assertContains(response, "Afasy Ltd.")
        self.assertContains(response, "BDT \u09F390,000.00 profit")
        self.assertContains(response, "CAD equivalent: CAD $1,058.82")
        groups = response.context["profit_overview"]["top_profit_customer_groups"]
        self.assertEqual([group["currency"] for group in groups], ["CAD", "USD", "BDT"])

    def test_missing_exchange_rate_shows_cad_equivalent_unavailable(self):
        self._customer_profit(
            customer_name="Afasy Ltd.",
            currency="BDT",
            revenue=Decimal("120000.00"),
            cost=Decimal("30000.00"),
            profit=Decimal("90000.00"),
        )

        response = self.client.get(reverse("ceo_operations_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BDT \u09F390,000.00 profit")
        self.assertContains(response, "CAD equivalent unavailable")

    def test_bdt_invoice_detail_shows_original_and_cad_equivalent_when_rate_exists(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85.00"))
        customer = Customer.objects.create(account_brand="BDT Invoice Client", contact_name="BDT Buyer")
        invoice = Invoice.objects.create(
            customer=customer,
            invoice_number="INV-BDT-DETAIL",
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            subtotal=Decimal("90000.00"),
            total_amount=Decimal("90000.00"),
            paid_amount=Decimal("0.00"),
            status="draft",
        )

        response = self.client.get(reverse("invoice_view", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u09F390,000.00")
        self.assertContains(response, "CAD equivalent: CAD $1,058.82")

    def test_bdt_invoice_detail_shows_cad_equivalent_unavailable_without_rate(self):
        customer = Customer.objects.create(account_brand="BDT Invoice Client", contact_name="BDT Buyer")
        invoice = Invoice.objects.create(
            customer=customer,
            invoice_number="INV-BDT-NO-RATE",
            currency="BDT",
            invoice_region="BD",
            invoice_market="bangladesh",
            subtotal=Decimal("90000.00"),
            total_amount=Decimal("90000.00"),
            paid_amount=Decimal("0.00"),
            status="draft",
        )

        response = self.client.get(reverse("invoice_view", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u09F390,000.00")
        self.assertContains(response, "CAD equivalent unavailable")


class LifecycleProfitCurrencyTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(account_brand="Lifecycle Client", contact_name="Lifecycle Buyer")
        self.lead = Lead.objects.create(customer=self.customer, account_brand="Lifecycle Client")
        self.opportunity = Opportunity.objects.create(lead=self.lead, customer=self.customer, product_type="Other")
        self.order = ProductionOrder.objects.create(
            opportunity=self.opportunity,
            customer=self.customer,
            title="Lifecycle production",
            order_code="POLIFECYCLE001",
            qty_total=100,
            factory_location="bd",
        )
        self.invoice = Invoice.objects.create(
            order=self.order,
            customer=self.customer,
            invoice_number="INV-LIFECYCLE-CAD",
            currency="CAD",
            subtotal=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            shipping_amount=Decimal("0.00"),
            paid_amount=Decimal("0.00"),
            status="sent",
        )
        self.lifecycle = OrderLifecycle.objects.create(
            customer=self.customer,
            lead=self.lead,
            opportunity=self.opportunity,
            invoice=self.invoice,
            production_order=self.order,
            status="invoice",
        )

    def test_cad_invoice_with_bdt_actual_cost_converts_cost_before_profit(self):
        ExchangeRate.objects.create(cad_to_bdt=Decimal("85.00"))
        ActualCostEntry.objects.create(
            production_order=self.order,
            opportunity=self.opportunity,
            section="sewing",
            item_name="Sewing",
            actual_total_cost=Decimal("85000.00"),
        )

        breakdown = build_lifecycle_profit_breakdown(self.lifecycle)

        self.assertTrue(breakdown["cost_available"])
        self.assertTrue(breakdown["can_use_actual_production"])
        self.assertEqual(breakdown["actual_production_cost"], Decimal("85000.00"))
        self.assertEqual(breakdown["actual_production_cost_for_profit"], Decimal("1000.00"))
        self.assertEqual(breakdown["total_cost"], Decimal("1000.00"))
        self.assertEqual(breakdown["net_profit"], Decimal("1500.00"))

    def test_missing_cost_exchange_rate_shows_profit_unavailable(self):
        ActualCostEntry.objects.create(
            production_order=self.order,
            opportunity=self.opportunity,
            section="sewing",
            item_name="Sewing",
            actual_total_cost=Decimal("85000.00"),
        )

        breakdown = build_lifecycle_profit_breakdown(self.lifecycle)

        self.assertFalse(breakdown["cost_available"])
        self.assertFalse(breakdown["is_comparable"])
        self.assertIsNone(breakdown["total_cost"])
        self.assertIsNone(breakdown["net_profit"])
        self.assertIn("stored CAD exchange rate", breakdown["comparison_reason"])


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
        audit = CRMAuditLog.objects.filter(
            module="invoice",
            record_id=str(self.invoice.pk),
            action_type=CRMAuditLog.ACTION_STATUS_CHANGED,
            previous_value="sent",
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("cancelled", audit.new_value)
        self.assertIn("Client cancelled", audit.new_value)
