from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group
from django.db import connection
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.tests import test_invoice_payment_live_fix
from crm.models import FinanceOperation, InvoicePayment, JournalEntry, JournalLine, Opportunity, ProductionOrder, FinancialPeriod
from crm.services.finance_operations import review_operation, FinanceOperationError
from crm.services.production_payment import production_payment_requirement, invoice_payment_summary
from crm.services.payment_reconciliation import record_invoice_payment, PaymentWriteError


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=True, FINANCIAL_CORE_REPORTING_ACTIVE=True)
class VerifiedInvoicePaymentTests(TestCase):
    setUpTestData = classmethod(test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.setUpTestData.__func__)
    invoice = test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.invoice
    issued_invoice = test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.issued_invoice
    payment_data = test_invoice_payment_live_fix.InvoicePaymentLiveFixTests.payment_data

    def setUp(self):
        self.client.force_login(self.ceo)

    def submit(self, invoice, amount, **kwargs):
        response = self.client.post(reverse('invoice_payment_add', args=[invoice.pk]), self.payment_data(invoice, amount, **kwargs))
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['payment_form'].errors)
        return FinanceOperation.objects.filter(invoice=invoice).latest('pk')

    def verify(self, operation, actor=None):
        return review_operation(operation, actor=actor or self.ceo, action='APPROVE', notes='Money arrived; bank transaction checked.', confirm_self_approval=True)

    def finance_user(self):
        user = type(self.ceo).objects.create_user(username='verifier')
        Group.objects.get_or_create(name='Finance')[0].user_set.add(user)
        user.access.role = 'CA'
        user.access.can_accounting_bd = False
        user.access.can_accounting_ca = True
        user.access.save()
        return user

    def test_sales_entry_finance_verification_and_remaining_balance(self):
        Group.objects.get_or_create(name='Sales')[0].user_set.add(self.employee)
        self.employee.access.role = 'CA'
        self.employee.access.save()
        opportunity = Opportunity.objects.create(assigned_to=self.employee, customer=self.customer)
        invoice = self.invoice('VERIFY-DEPOSIT', total='100')
        invoice.opportunity = opportunity
        invoice.deposit_percentage = Decimal('30')
        invoice.save()
        from crm.services.financial_permissions import can_access_invoice, can_submit_finance_operation, accessible_financial_sides
        actor = type(self.employee).objects.get(pk=self.employee.pk)
        self.assertTrue(can_submit_finance_operation(actor, 'CUSTOMER_PAYMENT'), accessible_financial_sides(actor))
        self.assertTrue(can_access_invoice(actor, invoice))
        self.client.force_login(self.employee)
        operation = self.submit(invoice, '30', reference='DEPOSIT')
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 0)
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())
        self.assertFalse(production_payment_requirement(invoice)['allowed'])
        self.assertEqual(invoice_payment_summary(invoice)['payment_status'], 'AWAITING VERIFICATION')
        with self.assertRaises(FinanceOperationError):
            self.verify(operation, self.employee)
        posted = self.verify(operation, self.finance_user())
        invoice.refresh_from_db()
        self.assertEqual(posted.state, 'POSTED')
        self.assertEqual(invoice.paid_amount, 30)
        self.assertEqual(invoice.balance, 70)
        self.assertEqual(posted.opportunity_id, opportunity.pk)
        self.assertEqual(posted.source_record.created_by_id, self.employee.pk)
        self.assertTrue(posted.approved_at)
        self.assertEqual(invoice_payment_summary(invoice)['payment_status'], 'DEPOSIT CONFIRMED')
        self.assertTrue(production_payment_requirement(invoice)['allowed'])
        self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())
        final = self.submit(invoice, '70', reference='BALANCE')
        self.verify(final)
        invoice.refresh_from_db()
        self.assertEqual(invoice_payment_summary(invoice)['payment_status'], 'FULLY PAID')
        self.assertEqual(invoice.balance, 0)

    def test_verification_rolls_back_when_journal_fails(self):
        invoice = self.invoice('VERIFY-ROLLBACK')
        operation = self.submit(invoice, '100')
        journals_before = JournalEntry.objects.count()
        with patch('crm.services.receivable_accounting.post_journal', side_effect=ValueError('posting failed')):
            with self.assertRaises(ValueError):
                self.verify(operation)
        operation.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(operation.state, 'PENDING')
        self.assertIsNone(operation.approved_by_id)
        self.assertEqual(invoice.paid_amount, 0)
        self.assertEqual(JournalEntry.objects.count(), journals_before)
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())

    def test_closed_period_after_entry_blocks_verification(self):
        invoice = self.invoice('VERIFY-CLOSED')
        operation = self.submit(invoice, '100')
        FinancialPeriod.objects.update(state='CLOSED')
        with self.assertRaises(ValueError):
            self.verify(operation)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'PENDING')
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())

    def test_bangladesh_legacy_and_core_amounts_match(self):
        invoice = self.invoice('VERIFY-BD', total='1000', side='BD', currency='BDT')
        operation = self.submit(invoice, '500', method='bank_transfer', account=self.bd_bank)
        self.verify(operation)
        payment = InvoicePayment.objects.get(invoice=invoice)
        self.assertEqual(payment.accounting_entry.amount_cad, Decimal('5.00'))
        totals = payment.receivable_event.financial_journal.lines.aggregate(debit=Sum('cad_debit'), credit=Sum('cad_credit'))
        self.assertEqual(totals, {'debit': Decimal('5'), 'credit': Decimal('5')})

    def test_duplicate_verification_cannot_create_second_journal(self):
        invoice = self.invoice('VERIFY-DUP')
        operation = self.submit(invoice, '100')
        self.verify(operation)
        count = JournalEntry.objects.count()
        with self.assertRaises(FinanceOperationError):
            self.verify(operation)
        self.assertEqual(JournalEntry.objects.count(), count)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 1)

    def test_under_deposit_never_unlocks_production(self):
        invoice = self.invoice('VERIFY-SMALL', total='100')
        invoice.deposit_percentage = Decimal('30')
        invoice.save()
        self.verify(self.submit(invoice, '10'))
        invoice.refresh_from_db()
        self.assertFalse(production_payment_requirement(invoice)['allowed'])
        self.assertEqual(invoice_payment_summary(invoice)['payment_status'], 'PARTIALLY PAID')

    def test_wrong_country_finance_cannot_verify(self):
        invoice = self.invoice('VERIFY-SCOPE', side='BD', currency='BDT')
        operation = self.submit(invoice, '100', method='bank_transfer', account=self.bd_bank)
        with self.assertRaises(FinanceOperationError):
            self.verify(operation, self.finance_user())

    def test_core_disabled_verification_stays_pending(self):
        invoice = self.invoice('VERIFY-DISABLED')
        operation = self.submit(invoice, '100')
        with override_settings(FINANCIAL_CORE_WRITES_ENABLED=False):
            with self.assertRaises(FinanceOperationError):
                self.verify(operation)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'PENDING')
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())

    def test_invalid_date_renders_validation_without_500(self):
        invoice = self.invoice('VERIFY-BAD-DATE')
        data = self.payment_data(invoice, '10')
        data['payment_date'] = '2026-99-99'
        response = self.client.post(reverse('invoice_payment_add', args=[invoice.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('payment_date', response.context['payment_form'].errors)
        self.assertFalse(FinanceOperation.objects.filter(invoice=invoice).exists())

    def test_entry_and_verification_are_distinct_even_for_ceo(self):
        invoice = self.invoice('VERIFY-CEO-ENTRY')
        operation = self.submit(invoice, '100')
        self.assertEqual(operation.state, 'PENDING')
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())
        with self.assertRaises(FinanceOperationError):
            review_operation(operation, actor=self.ceo, action='APPROVE', notes='Money checked.')
        self.verify(operation)
        self.assertEqual(InvoicePayment.objects.filter(invoice=invoice).count(), 1)

    def test_sales_cannot_access_another_employees_invoice(self):
        Group.objects.get_or_create(name='Sales')[0].user_set.add(self.employee)
        self.employee.access.role = 'CA'
        self.employee.access.save()
        invoice = self.invoice('VERIFY-UNOWNED')
        self.client.force_login(self.employee)
        response = self.client.post(reverse('invoice_payment_add', args=[invoice.pk]), self.payment_data(invoice, '10'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FinanceOperation.objects.filter(invoice=invoice).exists())

    def test_account_deactivated_after_entry_blocks_posting(self):
        invoice = self.invoice('VERIFY-INACTIVE')
        operation = self.submit(invoice, '100')
        self.ca_bank.is_active = False
        self.ca_bank.save()
        with self.assertRaises(ValueError):
            self.verify(operation)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'PENDING')
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())

    def test_payment_list_has_no_per_row_queries(self):
        from crm.models import FinanceOperation
        from crm.services.finance_operations import submit_operation
        invoice = self.invoice('VERIFY-LIST')
        first = self.submit(invoice, '1', reference='ONE')
        self.verify(first)
        for index in range(5):
            self.submit(invoice, '1', reference=f'PENDING-{index}')
        with CaptureQueriesContext(connection) as queries:
            labels = [row.finance_status_label for row in FinanceOperation.objects.select_related('invoice')]
        self.assertEqual(len(queries), 1)
        self.assertIn('AWAITING VERIFICATION', labels)

    def test_payment_entry_preserves_other_roles_permissions(self):
        from crm.services.financial_permissions import can_submit_finance_operation
        self.employee.groups.add(Group.objects.get_or_create(name='Sales')[0], Group.objects.get_or_create(name='HR')[0])
        self.employee.access.role = 'CA'
        self.employee.access.save()
        actor = type(self.employee).objects.get(pk=self.employee.pk)
        self.assertTrue(can_submit_finance_operation(actor, 'CUSTOMER_PAYMENT'))
        self.assertTrue(can_submit_finance_operation(actor, 'PAYROLL'))
        self.assertFalse(can_submit_finance_operation(actor, 'CUSTOMER_REFUND'))

    def test_employee_cannot_bypass_verification_via_posting_service(self):
        invoice = self.invoice('VERIFY-SERVICE-PERMISSION')
        payment = InvoicePayment(payment_date='2026-08-31', amount=Decimal('100'), currency='CAD', side='CA', payment_method='bank_transfer', rate_to_cad=1, rate_to_bdt=100)
        with self.assertRaises(PaymentWriteError):
            record_invoice_payment(invoice, payment, actor=self.employee, payment_account=self.ca_bank)
        self.assertFalse(InvoicePayment.objects.filter(invoice=invoice).exists())
