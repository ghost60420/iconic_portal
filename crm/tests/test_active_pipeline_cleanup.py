from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Customer, Invoice, InvoicePayment, Lead, Opportunity, ProductionOrder, Shipment
from crm.services.operations_search import search_operations_records
from crm.services.production_operational_status import OPERATIONAL_STATUS_SHIPPED


class ActivePipelineCleanupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="active-pipeline-admin",
            email="active-pipeline@example.com",
            password="test-pass",
        )
        cls.customer = Customer.objects.create(
            account_brand="History Client",
            contact_name="History Buyer",
            email="history@example.com",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def lead(self, suffix, **overrides):
        values = {
            "lead_id": f"LEAD-{suffix}",
            "account_brand": f"Brand {suffix}",
            "contact_name": f"Buyer {suffix}",
            "email": f"{suffix.lower()}@example.com",
            "customer": self.customer,
            "lead_status": "New",
        }
        values.update(overrides)
        return Lead.objects.create(**values)

    def opportunity(self, lead, suffix, **overrides):
        values = {
            "lead": lead,
            "customer": self.customer,
            "opportunity_id": f"OPP-{suffix}",
            "stage": "Prospecting",
            "is_open": True,
            "product_type": "Streetwear",
            "product_category": "Hoodie",
            "order_value": Decimal("1200.00"),
            "order_currency": "CAD",
        }
        values.update(overrides)
        return Opportunity.objects.create(**values)

    def production_order(self, suffix, **overrides):
        values = {
            "customer": self.customer,
            "title": f"Production {suffix}",
            "order_code": f"PO-TEST-{suffix}",
            "qty_total": 100,
            "operational_status": "planning",
        }
        values.update(overrides)
        return ProductionOrder.objects.create(**values)

    def test_lead_default_hides_converted_and_filters_restore_history(self):
        active = self.lead("ACTIVE")
        converted = self.lead("CONVERTED", lead_status="Converted")
        self.opportunity(converted, "CONVERTED")

        default_response = self.client.get(reverse("leads_list"))
        default_ids = [lead.pk for lead in default_response.context["page_obj"].object_list]
        self.assertIn(active.pk, default_ids)
        self.assertNotIn(converted.pk, default_ids)

        converted_response = self.client.get(reverse("leads_list"), {"lead_status": "converted"})
        converted_ids = [lead.pk for lead in converted_response.context["page_obj"].object_list]
        self.assertIn(converted.pk, converted_ids)

        all_response = self.client.get(reverse("leads_list"), {"lead_status": "all"})
        all_ids = [lead.pk for lead in all_response.context["page_obj"].object_list]
        self.assertIn(active.pk, all_ids)
        self.assertIn(converted.pk, all_ids)

        results = [
            row
            for label, rows in search_operations_records(self.user, converted.lead_id)
            if label == "Leads"
            for row in rows
        ]
        self.assertEqual([row["number"] for row in results], [converted.lead_id])
        self.assertEqual(results[0]["status"], "Converted")

    def test_archived_and_closed_leads_are_filterable_without_delete(self):
        closed = self.lead("CLOSED", lead_status="Lost")
        archived = self.lead("ARCHIVED", is_archived=True)

        default_response = self.client.get(reverse("leads_list"))
        default_ids = [lead.pk for lead in default_response.context["page_obj"].object_list]
        self.assertNotIn(closed.pk, default_ids)
        self.assertNotIn(archived.pk, default_ids)

        closed_response = self.client.get(reverse("leads_list"), {"lead_status": "closed"})
        self.assertIn(closed.pk, [lead.pk for lead in closed_response.context["page_obj"].object_list])

        archived_response = self.client.get(reverse("leads_list"), {"lead_status": "archived"})
        self.assertIn(archived.pk, [lead.pk for lead in archived_response.context["page_obj"].object_list])

        self.assertTrue(Lead.objects.filter(pk=closed.pk).exists())
        self.assertTrue(Lead.objects.filter(pk=archived.pk).exists())

    def test_opportunity_default_hides_moved_to_production_and_filters_restore(self):
        active_lead = self.lead("OPP-ACTIVE")
        moved_lead = self.lead("OPP-MOVED", lead_status="Converted")
        active = self.opportunity(active_lead, "ACTIVE")
        moved = self.opportunity(moved_lead, "MOVED", stage="Production")
        order = self.production_order("MOVED", lead=moved_lead, opportunity=moved)

        default_response = self.client.get(reverse("opportunities_list"))
        default_ids = [opp.pk for opp in default_response.context["page_obj"].object_list]
        self.assertIn(active.pk, default_ids)
        self.assertNotIn(moved.pk, default_ids)

        moved_response = self.client.get(reverse("opportunities_list"), {"status": "moved_to_production"})
        self.assertIn(moved.pk, [opp.pk for opp in moved_response.context["page_obj"].object_list])

        all_response = self.client.get(reverse("opportunities_list"), {"status": "all"})
        self.assertIn(moved.pk, [opp.pk for opp in all_response.context["page_obj"].object_list])

        production_detail = self.client.get(reverse("production_detail", args=[order.pk]))
        self.assertEqual(production_detail.status_code, 200)
        self.assertContains(production_detail, moved.opportunity_id)

        results = [
            row
            for label, rows in search_operations_records(self.user, moved.opportunity_id)
            if label == "Opportunities"
            for row in rows
        ]
        self.assertEqual([row["number"] for row in results], [moved.opportunity_id])
        self.assertEqual(results[0]["status"], "Moved to Production")

    def test_production_default_hides_completed_and_filters_restore(self):
        active = self.production_order("ACTIVE")
        completed = self.production_order("COMPLETE", operational_status=OPERATIONAL_STATUS_SHIPPED)
        Shipment.objects.create(
            order=completed,
            customer=self.customer,
            status="delivered",
            delivered_at=timezone.now(),
            ship_date=timezone.localdate(),
        )

        default_response = self.client.get(reverse("production_list"))
        default_ids = [row["order"].pk for row in default_response.context["orders_data"]]
        self.assertIn(active.pk, default_ids)
        self.assertNotIn(completed.pk, default_ids)

        completed_response = self.client.get(reverse("production_list"), {"status": "completed"})
        self.assertIn(completed.pk, [row["order"].pk for row in completed_response.context["orders_data"]])

        all_response = self.client.get(reverse("production_list"), {"status": "all"})
        self.assertIn(completed.pk, [row["order"].pk for row in all_response.context["orders_data"]])

        results = [
            row
            for label, rows in search_operations_records(self.user, completed.purchase_order_number)
            if label == "Production"
            for row in rows
        ]
        self.assertEqual([row["number"] for row in results], [completed.purchase_order_number])
        self.assertEqual(results[0]["status"], "Completed")

    def test_delivered_shipment_syncs_production_to_completed_history(self):
        order = self.production_order("SHIPMENT-SYNC")
        shipment = Shipment.objects.create(order=order, customer=self.customer, status="planned")

        response = self.client.post(
            reverse("shipment_detail", args=[shipment.pk]),
            {"action": "update_status", "status": "delivered"},
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, "delivered")
        self.assertEqual(order.status, "done")
        self.assertEqual(order.operational_status, OPERATIONAL_STATUS_SHIPPED)

        production_response = self.client.get(reverse("production_list"))
        default_ids = [row["order"].pk for row in production_response.context["orders_data"]]
        self.assertNotIn(order.pk, default_ids)

        customer_response = self.client.get(reverse("customer_detail", args=[self.customer.pk]))
        self.assertIn(order, list(customer_response.context["production_completed"]))

    def test_customer_profile_preserves_full_history_sections(self):
        lead = self.lead("PROFILE", lead_status="Converted")
        opportunity = self.opportunity(lead, "PROFILE", stage="Production")
        order = self.production_order("PROFILE", lead=lead, opportunity=opportunity, operational_status=OPERATIONAL_STATUS_SHIPPED)
        shipment = Shipment.objects.create(
            order=order,
            customer=self.customer,
            opportunity=opportunity,
            status="delivered",
            delivered_at=timezone.now(),
            ship_date=timezone.localdate(),
            tracking_number="TRACK-HISTORY",
        )
        invoice = Invoice.objects.create(
            invoice_number="INV-HISTORY",
            customer=self.customer,
            opportunity=opportunity,
            order=order,
            currency="CAD",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            paid_amount=Decimal("25.00"),
            status="partial",
        )
        payment = InvoicePayment.objects.create(
            invoice=invoice,
            production_order=order,
            amount=Decimal("25.00"),
            currency="CAD",
            side="CA",
        )

        response = self.client.get(reverse("customer_detail", args=[self.customer.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead history")
        self.assertContains(response, "Opportunity history")
        self.assertContains(response, "Production order history")
        self.assertContains(response, "Invoice history")
        self.assertContains(response, "Shipment history")
        self.assertContains(response, "Payment history")
        self.assertContains(response, lead.lead_id)
        self.assertContains(response, opportunity.opportunity_id)
        self.assertContains(response, order.purchase_order_number)
        self.assertContains(response, shipment.tracking_number)
        self.assertContains(response, invoice.invoice_number)
        self.assertIn(payment, list(response.context["payment_history"]))

    def test_lifecycle_banners_explain_hidden_active_records(self):
        lead = self.lead("BANNER", lead_status="Converted")
        opportunity = self.opportunity(lead, "BANNER", stage="Production")
        order = self.production_order("BANNER", lead=lead, opportunity=opportunity, operational_status=OPERATIONAL_STATUS_SHIPPED)
        shipment = Shipment.objects.create(
            order=order,
            customer=self.customer,
            opportunity=opportunity,
            status="delivered",
            delivered_at=timezone.now(),
            ship_date=timezone.localdate(),
            tracking_number="TRACK-BANNER",
        )

        lead_response = self.client.get(reverse("lead_detail", args=[lead.pk]))
        self.assertEqual(lead_response.status_code, 200)
        self.assertContains(lead_response, "Converted to Opportunity")
        self.assertContains(lead_response, opportunity.opportunity_id)
        self.assertContains(lead_response, "This lead no longer appears in the active Lead List.")

        opportunity_response = self.client.get(reverse("opportunity_detail", args=[opportunity.pk]))
        self.assertEqual(opportunity_response.status_code, 200)
        self.assertContains(opportunity_response, "Moved to Production")
        self.assertContains(opportunity_response, order.purchase_order_number)
        self.assertContains(opportunity_response, "This opportunity no longer appears in the active Opportunity List.")

        production_response = self.client.get(reverse("production_detail", args=[order.pk]))
        self.assertEqual(production_response.status_code, 200)
        self.assertContains(production_response, "Shipment completed on")
        self.assertContains(production_response, shipment.tracking_number)
        self.assertContains(production_response, "This order now appears in Client History.")

    def test_main_dashboard_active_counts_exclude_completed_and_converted_records(self):
        active_lead = self.lead("DASH-ACTIVE")
        converted_lead = self.lead("DASH-CONVERTED", lead_status="Converted")
        active_opp = self.opportunity(active_lead, "DASH-ACTIVE")
        moved_opp = self.opportunity(converted_lead, "DASH-MOVED", stage="Production")
        self.production_order("DASH-MOVED", lead=converted_lead, opportunity=moved_opp)
        completed_order = self.production_order("DASH-COMPLETE", operational_status=OPERATIONAL_STATUS_SHIPPED)
        Shipment.objects.create(
            order=completed_order,
            customer=self.customer,
            status="delivered",
            delivered_at=timezone.now(),
            ship_date=timezone.localdate(),
        )

        response = self.client.get(reverse("main_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_opps"], 1)
        self.assertEqual(response.context["active_production_count"], 1)
        self.assertEqual(response.context["completed_production_count"], 1)
