from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from crm.models import Lead, Opportunity, Invoice
from crm.models_access import UserAccess


class OpportunityStatusTests(TestCase):
    def setUp(self):
        self.lead = Lead.objects.create(account_brand="Test Brand")

    def test_opportunity_status_label(self):
        opp_open = Opportunity.objects.create(
            lead=self.lead,
            stage="Prospecting",
            is_open=True,
        )
        opp_won = Opportunity.objects.create(
            lead=self.lead,
            stage="Closed Won",
            is_open=False,
        )
        opp_lost = Opportunity.objects.create(
            lead=self.lead,
            stage="Closed Lost",
            is_open=False,
        )

        self.assertEqual(opp_open.status_label, "Open")
        self.assertEqual(opp_won.status_label, "Closed Won")
        self.assertEqual(opp_lost.status_label, "Closed Lost")


class InvoiceTemplateTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="acct", password="pass1234")
        access, _ = UserAccess.objects.get_or_create(user=self.user)
        access.can_accounting_bd = True
        access.can_accounting_ca = False
        access.save()

        self.invoice_ca = Invoice.objects.create(
            invoice_number="INV10001",
            currency="CAD",
            invoice_region="CA",
        )
        self.invoice_bd = Invoice.objects.create(
            invoice_number="INV10002",
            currency="BDT",
            invoice_region="BD",
        )

    def test_ca_template_selection_and_content(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("invoice_view", args=[self.invoice_ca.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "crm/invoice/invoice_ca.html")
        self.assertContains(resp, "Payment Instructions Canada")
        self.assertContains(resp, "forhadhossain604@gmail.com")
        self.assertContains(resp, "PayPal ID: iconicapparelhouse")

    def test_bd_template_selection_and_no_payment_terms(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("invoice_view", args=[self.invoice_bd.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "crm/invoice/invoice_bd.html")
        self.assertNotContains(resp, "PayPal")
        self.assertNotContains(resp, "E Transfer")
        self.assertNotContains(resp, "Payment Instructions")


class InvoiceApprovalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.approver = User.objects.create_user(username="approver", password="pass1234")
        self.blocked = User.objects.create_user(username="blocked", password="pass1234")

        approver_access, _ = UserAccess.objects.get_or_create(user=self.approver)
        approver_access.can_accounting_bd = True
        approver_access.can_accounting_ca = False
        approver_access.save()

        blocked_access, _ = UserAccess.objects.get_or_create(user=self.blocked)
        blocked_access.can_accounting_bd = False
        blocked_access.can_accounting_ca = False
        blocked_access.save()

        self.invoice = Invoice.objects.create(
            invoice_number="INV20001",
            currency="CAD",
            invoice_region="CA",
        )

    def test_user_without_permission_cannot_approve(self):
        self.client.force_login(self.blocked)
        resp = self.client.post(reverse("invoice_approve", args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_user_with_permission_can_approve(self):
        self.client.force_login(self.approver)
        resp = self.client.post(reverse("invoice_approve", args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.invoice_status, "APPROVED")

    def test_approved_invoice_cannot_be_edited(self):
        self.invoice.invoice_status = "APPROVED"
        self.invoice.save(update_fields=["invoice_status"])
        self.client.force_login(self.approver)
        resp = self.client.get(reverse("invoice_edit", args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 403)
