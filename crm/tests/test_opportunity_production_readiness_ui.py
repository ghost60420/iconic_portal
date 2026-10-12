from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import Customer, Invoice, Opportunity, ProductionOrder, QuickCosting
from crm.services.production_approval import approve_production_release


class OpportunityProductionReadinessUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.ceo = users.objects.create_superuser(username="readiness-ceo", email="ceo@example.test", password="test")
        cls.sales = users.objects.create_user(username="readiness-sales", password="test")
        cls.sales.groups.add(Group.objects.get_or_create(name="Sales")[0])
        cls.finance = users.objects.create_user(username="readiness-finance", password="test")
        cls.finance.groups.add(Group.objects.get_or_create(name="Finance")[0])
        for user in (cls.sales, cls.finance):
            user.access.can_opportunities = True
            user.access.role = "CA"
            user.access.can_accounting_ca = user.pk == cls.finance.pk
            user.access.can_accounting_bd = False
            user.access.save()
        cls.customer = Customer.objects.create(account_brand="Readiness Customer")
        cls.opportunity = Opportunity.objects.create(customer=cls.customer, assigned_to=cls.sales, moq_units=50)
        cls.quick = QuickCosting.objects.create(
            opportunity=cls.opportunity, project_name="Sample with many pieces",
            costing_purpose=QuickCosting.PURPOSE_SAMPLE, pricing_type=QuickCosting.PRICING_FULL_PACKAGE,
            quantity=50, currency="CAD", selling_price_per_piece=Decimal("10"),
            salesperson=cls.sales, status=QuickCosting.STATUS_INVOICED,
            approved_by=cls.ceo, approved_at=timezone.now(),
            quotation_number="QQT-READINESS", quoted_at=timezone.now(), quoted_by=cls.ceo,
        )
        cls.invoice = Invoice.objects.create(
            opportunity=cls.opportunity, quick_costing=cls.quick, customer=cls.customer,
            invoice_number="INV-READINESS", currency="CAD", invoice_region="CA",
            invoice_type="sample", subtotal=Decimal("500"), total_amount=Decimal("500"),
            paid_amount=Decimal("0"), deposit_percentage=Decimal("30"),
        )

    def detail(self, user=None):
        self.client.force_login(user or self.ceo)
        response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def test_one_readiness_section_shows_full_sample_requirement_and_exact_blockers(self):
        response = self.detail()
        self.assertContains(response, 'id="opp-production-readiness"', count=1)
        self.assertContains(response, "Full sample payment must be verified.")
        self.assertContains(response, "ACTION REQUIRED")
        self.assertNotContains(response, "Production release approval is pending.")
        self.assertContains(response, "Required payment")
        self.assertEqual(response.context["production_readiness"]["required_amount"], Decimal("500"))
        self.assertNotContains(response, "Payment does not block production")
        self.assertNotContains(response, "Can this move to the factory?")
        self.assertNotContains(response, "READY FOR PRODUCTION")
        self.assertFalse(ProductionOrder.objects.filter(opportunity=self.opportunity).exists())

    def test_commercial_quote_summary_recognizes_the_approved_quick_quotation(self):
        response = self.detail()
        self.assertContains(response, "Production quotation status")
        self.assertContains(response, "QQT-READINESS")
        self.assertNotContains(response, "No quote yet")
        self.assertTrue(response.context["production_readiness"]["quotation_approved"])

    def test_sales_sees_blockers_without_ceo_or_finance_actions(self):
        response = self.detail(self.sales)
        self.assertContains(response, "Blocking production")
        self.assertNotContains(response, 'id="opp-production-exception"')
        self.assertNotContains(response, "Approve production release")
        self.assertNotContains(response, "Verify customer payment")

    def test_finance_can_review_payment_without_production_authority(self):
        # Manager already grants opportunity access; Finance alone does not.
        self.finance.groups.add(Group.objects.get_or_create(name="Manager")[0])
        response = self.detail(self.finance)
        self.assertContains(response, "Verify customer payment")
        self.assertNotContains(response, 'id="opp-production-exception"')
        self.assertNotContains(response, "Approve production release")

    def test_finance_only_does_not_gain_opportunity_access(self):
        self.client.force_login(self.finance)
        response = self.client.get(reverse("opportunity_detail", args=[self.opportunity.pk]))
        self.assertEqual(response.status_code, 403)

    def test_ceo_exception_remains_visibly_unpaid_and_requires_a_separate_release_post(self):
        approve_production_release(self.opportunity, user=self.ceo, reason="Customer sample deadline approved.", waived_requirements=["payment"])
        response = self.detail()
        self.assertContains(response, "READY FOR PRODUCTION")
        self.assertContains(response, "Requirement waived; invoice payment status is unchanged")
        self.assertContains(response, "Customer sample deadline approved.")
        self.assertContains(response, "readiness-ceo")
        self.assertContains(response, 'name="action" value="release"')
        self.assertFalse(response.context["production_readiness"]["payment_verified"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0"))
        self.assertFalse(ProductionOrder.objects.filter(opportunity=self.opportunity).exists())

    def test_missing_quotation_is_not_hidden_by_an_approved_costing(self):
        QuickCosting.objects.filter(pk=self.quick.pk).update(quotation_number="", quoted_at=None, status=QuickCosting.STATUS_APPROVED)
        response = self.detail()
        self.assertContains(response, "Quotation approval is pending.")
        self.assertContains(response, "Create quotation")
        self.assertNotContains(response, "READY FOR PRODUCTION")

    def test_ceo_gets_existing_costing_approval_action_and_required_exception_reason(self):
        QuickCosting.objects.filter(pk=self.quick.pk).update(status=QuickCosting.STATUS_SUBMITTED, approved_at=None, approved_by=None)
        response = self.detail()
        self.assertContains(response, reverse("quick_costing_approve", args=[self.quick.pk]))
        self.assertContains(response, "Approve costing")
        self.assertContains(response, 'id="opp-production-exception-reason" name="reason" rows="3" required')
        self.assertContains(response, 'name="waived_requirements" value="costing"')

    def test_manager_can_move_paid_sample_without_repeated_commercial_approval(self):
        self.sales.groups.add(Group.objects.get_or_create(name="Manager")[0])
        Invoice.objects.filter(pk=self.invoice.pk).update(paid_amount=Decimal("500"))
        response = self.detail(self.sales)
        self.assertContains(response, "READY FOR PRODUCTION")
        self.assertContains(response, 'name="action" value="release"')
        self.assertNotContains(response, "Approve production release")
        self.assertNotContains(response, "Approve quotation")
        self.assertNotContains(response, 'id="opp-production-exception"')
        self.assertFalse(ProductionOrder.objects.exists())
