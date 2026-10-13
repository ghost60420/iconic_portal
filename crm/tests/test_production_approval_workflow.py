import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AccountingEntry, CRMAuditLog, CostingHeader, CostingLineItem, Customer,
    FinanceOperation, FinancialAuditEvent, Invoice, InvoiceFinancialState,
    InvoicePayment, JournalEntry, JournalLine, Opportunity, ProductionOrder, QuickCosting,
)
from crm.services.production_approval import (
    APPROVAL_FIELD, ProductionApprovalError, approve_production_release,
    assert_production_release, production_readiness,
)


class ProductionApprovalWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.ceo = users.objects.create_superuser(username="release-super", password="test")
        cls.employee = users.objects.create_user(username="release-employee")
        cls.admin = users.objects.create_user(username="release-admin")
        cls.named_ceo = users.objects.create_user(username="release-ceo")
        cls.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        cls.named_ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        cls.customer = Customer.objects.create(account_brand="Release Client", country="Canada")

    def source(self, *, paid="100", percentage="30", sample=False, **changes):
        opportunity = Opportunity.objects.create(customer=self.customer, assigned_to=self.employee)
        quick = QuickCosting.objects.create(
            opportunity=opportunity, project_name="Release sample" if sample else "Release bulk",
            quantity=2 if sample else 100, currency="CAD",
            costing_purpose=QuickCosting.PURPOSE_SAMPLE if sample else QuickCosting.PURPOSE_BULK,
            pricing_type=QuickCosting.PRICING_SAMPLE if sample else QuickCosting.PRICING_FULL_PACKAGE,
            status=QuickCosting.STATUS_INVOICED, approved_by=self.ceo, approved_at=timezone.now(),
            quotation_number=f"Q-REL-{opportunity.pk}", quoted_by=self.ceo, quoted_at=timezone.now(),
            selling_price_per_piece=Decimal("1"),
        )
        invoice = Invoice.objects.create(
            customer=self.customer, opportunity=opportunity, quick_costing=quick,
            invoice_number=f"INV-REL-{opportunity.pk}", currency="CAD", total_amount=Decimal("100"),
            paid_amount=Decimal(paid), deposit_percentage=Decimal(percentage),
            invoice_type="sample" if sample else "bulk", **changes,
        )
        return opportunity, quick, invoice

    def test_sample_full_payment_is_ready_without_repeat_ceo_approval(self):
        opportunity, quick, invoice = self.source(sample=True)
        readiness = production_readiness(opportunity, user=self.ceo)
        self.assertTrue(readiness["payment_verified"])
        self.assertEqual(readiness["required_amount"], Decimal("100"))
        self.assertEqual(readiness["blockers"], [])
        self.assertTrue(readiness["ready"])
        self.assertFalse(ProductionOrder.objects.exists())
        self.assertTrue(assert_production_release(opportunity, source=quick, invoice=invoice, user=self.ceo)["ready"])
        self.assertFalse(ProductionOrder.objects.exists())

    def test_sample_partial_or_unpaid_is_ready_without_misreporting_payment(self):
        for paid in ("0", "30", "99.99"):
            with self.subTest(paid=paid):
                opportunity, _, _ = self.source(sample=True, paid=paid, percentage="30")
                readiness = production_readiness(opportunity)
                self.assertFalse(readiness["payment_verified"])
                self.assertEqual(readiness["required_percentage"], 100)
                self.assertTrue(readiness["ready"])
                self.assertEqual(readiness["blockers"], [])
                self.assertTrue(approve_production_release(opportunity, user=self.ceo)["ready"])

    def test_sample_invoice_type_displays_full_payment_requirement_for_bulk_source(self):
        opportunity, _, invoice = self.source(paid="30")
        Invoice.objects.filter(pk=invoice.pk).update(invoice_type="sample")
        readiness = production_readiness(opportunity)
        self.assertTrue(readiness["is_sample"])
        self.assertEqual(readiness["required_amount"], 100)
        self.assertFalse(readiness["payment_verified"])
        self.assertTrue(readiness["ready"])

    def test_missing_quotation_blocks_fully_paid_sample(self):
        opportunity, quick, _ = self.source(sample=True)
        QuickCosting.objects.filter(pk=quick.pk).update(quotation_number="", quoted_at=None)
        self.assertFalse(production_readiness(opportunity)["quotation_approved"])
        with self.assertRaisesRegex(ProductionApprovalError, "Quotation approval"):
            approve_production_release(opportunity, user=self.ceo)

    def test_bulk_preserves_exact_invoice_terms_without_blocking_production(self):
        for percent in ("20", "37.5", "70"):
            with self.subTest(percent=percent):
                opportunity, _, invoice = self.source(paid=percent, percentage=percent)
                self.assertEqual(production_readiness(opportunity)["required_amount"], Decimal(percent))
                self.assertTrue(approve_production_release(opportunity, user=self.admin)["ready"])
                Invoice.objects.filter(pk=invoice.pk).update(paid_amount=Decimal(percent) - Decimal("0.01"))
                readiness = production_readiness(opportunity)
                self.assertFalse(readiness["payment_verified"])
                self.assertTrue(readiness["ready"])

    def test_ceo_exception_is_audited_without_financial_changes(self):
        opportunity, quick, invoice = self.source(paid="0", sample=True)
        QuickCosting.objects.filter(pk=quick.pk).update(quotation_number="", quoted_at=None)
        before = (Invoice.objects.values().get(pk=invoice.pk), InvoicePayment.objects.count(), JournalEntry.objects.count())
        result = approve_production_release(opportunity, user=self.named_ceo, reason="Client launch sample; CEO approves quotation exception.", waived_requirements=["quotation"])
        self.assertTrue(result["ready"])
        self.assertFalse(result["payment_verified"])
        self.assertEqual(result["waived_requirements"], ["quotation"])
        audit = result["release_approval"]
        payload = json.loads(audit.new_value)
        self.assertEqual(audit.actor_id, self.named_ceo.pk)
        self.assertIsNotNone(audit.created_at)
        self.assertEqual(payload["opportunity_id"], opportunity.pk)
        self.assertEqual(payload["waived_requirements"], ["quotation"])
        self.assertTrue(payload["reason"])
        self.assertEqual(before, (Invoice.objects.values().get(pk=invoice.pk), InvoicePayment.objects.count(), JournalEntry.objects.count()))
        self.assertFalse(ProductionOrder.objects.exists())

    def test_employee_and_admin_cannot_approve_exception(self):
        opportunity, quick, _ = self.source(paid="0")
        QuickCosting.objects.filter(pk=quick.pk).update(quotation_number="", quoted_at=None)
        for user in (self.employee, self.admin, None):
            with self.subTest(user=user), self.assertRaises(ProductionApprovalError):
                approve_production_release(opportunity, user=user, reason="Attempted bypass", waived_requirements=["quotation"])
        self.assertFalse(CRMAuditLog.objects.filter(field_name=APPROVAL_FIELD).exists())

    def test_exception_requires_reason_and_explicit_pending_requirements(self):
        opportunity, quick, _ = self.source(paid="0")
        QuickCosting.objects.filter(pk=quick.pk).update(quotation_number="", quoted_at=None)
        for reason, waivers in (("", ["quotation"]), ("x", ["invoice"]), ("x", ["costing"]), ("x", [])):
            with self.subTest(waivers=waivers, reason=reason), self.assertRaises(ProductionApprovalError):
                approve_production_release(opportunity, user=self.ceo, reason=reason, waived_requirements=waivers)

    def test_prior_exception_cannot_be_silently_replaced_by_normal_approval(self):
        opportunity, quick, _ = self.source(paid="0")
        QuickCosting.objects.filter(pk=quick.pk).update(quotation_number="", quoted_at=None)
        approved = approve_production_release(opportunity, user=self.ceo, reason="Quotation exception", waived_requirements=["quotation"])
        with self.assertRaisesRegex(ProductionApprovalError, "Quotation"):
            approve_production_release(opportunity, user=self.admin)
        self.assertEqual(production_readiness(opportunity)["release_approval"].pk, approved["release_approval"].pk)

    def test_invoice_terms_change_invalidates_release(self):
        opportunity, _, invoice = self.source(paid="30")
        approve_production_release(opportunity, user=self.ceo)
        Invoice.objects.filter(pk=invoice.pk).update(deposit_percentage=Decimal("40"))
        self.assertFalse(production_readiness(opportunity)["production_approved"])

    def test_source_change_invalidates_release(self):
        opportunity, quick, _ = self.source()
        approve_production_release(opportunity, user=self.ceo)
        QuickCosting.objects.filter(pk=quick.pk).update(quantity=999)
        self.assertFalse(production_readiness(opportunity)["production_approved"])

    def test_rejection_invalidates_prior_costing_exception(self):
        opportunity, quick, _ = self.source()
        QuickCosting.objects.filter(pk=quick.pk).update(status=QuickCosting.STATUS_DRAFT, approved_at=None)
        approve_production_release(opportunity, user=self.ceo, reason="Time-sensitive costing exception", waived_requirements=["costing", "quotation"])
        self.assertTrue(production_readiness(opportunity)["ready"])
        QuickCosting.objects.filter(pk=quick.pk).update(status=QuickCosting.STATUS_REJECTED)
        self.assertFalse(production_readiness(opportunity)["production_approved"])

    def test_new_revision_is_nonwaivable(self):
        opportunity, quick, _ = self.source()
        approve_production_release(opportunity, user=self.ceo)
        QuickCosting.objects.create(opportunity=opportunity, revision_root=quick, previous_revision=quick, revision_number=2, project_name="New revision")
        readiness = production_readiness(opportunity)
        self.assertIn("source", [item["key"] for item in readiness["blockers"]])
        with self.assertRaises(ProductionApprovalError):
            approve_production_release(opportunity, user=self.ceo)

    def test_release_reloads_payment_truth_without_blocking_reversed_payment(self):
        opportunity, quick, invoice = self.source()
        approve_production_release(opportunity, user=self.ceo)
        Invoice.objects.filter(pk=invoice.pk).update(paid_amount=0)
        readiness = assert_production_release(opportunity, source=quick, invoice=invoice, user=self.ceo)
        self.assertFalse(readiness["payment_verified"])
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["invoice"].paid_amount, 0)

    def test_old_invoice_cannot_override_latest_unpaid_invoice(self):
        opportunity, quick, invoice = self.source()
        approve_production_release(opportunity, user=self.ceo)
        Invoice.objects.create(customer=self.customer, opportunity=opportunity, quick_costing=quick, invoice_number="NEWER-INVOICE", total_amount=100)
        with self.assertRaisesRegex(ProductionApprovalError, "latest invoice"):
            assert_production_release(opportunity, source=quick, invoice=invoice, user=self.ceo)

    def test_closed_invoice_cannot_be_waived(self):
        opportunity, _, _ = self.source(status="cancelled")
        with self.assertRaisesRegex(ProductionApprovalError, "Cancelled"):
            approve_production_release(opportunity, user=self.ceo)

    def test_audit_failure_fails_approval_closed(self):
        opportunity, _, _ = self.source()
        with patch("crm.services.production_approval.CRMAuditLog.objects.create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                approve_production_release(opportunity, user=self.ceo)
        self.assertFalse(production_readiness(opportunity)["production_approved"])
        self.assertFalse(ProductionOrder.objects.exists())

    def test_repeated_approval_is_idempotent(self):
        opportunity, _, _ = self.source()
        first = approve_production_release(opportunity, user=self.ceo)
        second = approve_production_release(opportunity, user=self.ceo)
        self.assertEqual(first["release_approval"].pk, second["release_approval"].pk)
        self.assertEqual(CRMAuditLog.objects.filter(field_name=APPROVAL_FIELD).count(), 1)

    def test_readiness_queries_are_bounded_independent_of_audit_history(self):
        opportunity, _, _ = self.source()
        approve_production_release(opportunity, user=self.ceo)
        with CaptureQueriesContext(connection) as first:
            production_readiness(opportunity, user=self.ceo)
        CRMAuditLog.objects.bulk_create([
            CRMAuditLog(module="opportunities", record_id=str(opportunity.pk), action_type="updated", field_name="notes", new_value=str(index))
            for index in range(30)
        ])
        with CaptureQueriesContext(connection) as second:
            readiness = production_readiness(opportunity, user=self.ceo)
            _ = readiness["release_approval"].actor.username
        self.assertLessEqual(len(second), 8)
        self.assertEqual(len(first), len(second))

    def test_actor_required_even_with_valid_release(self):
        opportunity, quick, invoice = self.source()
        approve_production_release(opportunity, user=self.ceo)
        with self.assertRaises(ProductionApprovalError):
            assert_production_release(opportunity, source=quick, invoice=invoice)

    def test_cmt_model_guard_accepts_only_audited_costing_exception(self):
        opportunity, quick, invoice = self.source(paid="0")
        QuickCosting.objects.filter(pk=quick.pk).update(
            pricing_type=QuickCosting.PRICING_CMT, currency="BDT", status=QuickCosting.STATUS_DRAFT,
            approved_by=None, approved_at=None, quotation_number="", quoted_at=None,
            sewing_charge_per_piece_bdt=Decimal("1"),
        )
        Invoice.objects.filter(pk=invoice.pk).update(currency="BDT")
        quick.refresh_from_db()
        with self.assertRaises(ValidationError):
            ProductionOrder.objects.create(title="CMT unaudited", opportunity=opportunity, source_quick_costing=quick, order_type="sewing_charge")
        approve_production_release(
            opportunity, user=self.ceo, reason="CEO accepts documented sample rush and collection risk.",
            waived_requirements=["costing", "quotation"],
        )
        order = ProductionOrder.objects.create(title="CMT approved exception", opportunity=opportunity, source_quick_costing=quick, order_type="sewing_charge")
        self.assertTrue(order.pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 0)

    def test_full_sample_release_creates_one_production_record(self):
        from crm.services.production_orders import create_production_order_from_paid_full_package_quick_costing

        opportunity, quick, invoice = self.source(sample=True)
        approve_production_release(opportunity, user=self.ceo)
        first, created = create_production_order_from_paid_full_package_quick_costing(quick, invoice=invoice, user=self.ceo)
        duplicate, repeated = create_production_order_from_paid_full_package_quick_costing(quick, invoice=invoice, user=self.ceo)
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, duplicate.pk)
        self.assertEqual(first.production_order_type, "sampling")
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 100)
        self.assertEqual(invoice.order_id, first.pk)

    def test_manual_form_cannot_create_or_reassign_opportunity_order(self):
        from crm.production_forms import ProductionOrderForm

        opportunity, _, _ = self.source()
        manual_order = ProductionOrder.objects.create(title="Existing unlinked production")
        for instance in (None, manual_order):
            with self.subTest(instance=instance):
                form = ProductionOrderForm({"opportunity": opportunity.pk}, instance=instance)
                self.assertFalse(form.is_valid())
                self.assertIn("Production readiness", str(form.errors["opportunity"]))
        manual_order.refresh_from_db()
        self.assertIsNone(manual_order.opportunity_id)

    def test_advanced_costing_line_changes_invalidate_release(self):
        opportunity = Opportunity.objects.create(customer=self.customer)
        source = CostingHeader.objects.create(
            opportunity=opportunity, customer=self.customer, status="approved",
            approved_by=self.ceo, approved_at=timezone.now(), quotation_number="ADV-REL",
            quoted_by=self.ceo, quoted_at=timezone.now(), quotation_status="approved",
            quotation_approved_by=self.ceo, quotation_approved_at=timezone.now(),
        )
        line = CostingLineItem.objects.create(costing=source, item_name="Fabric", unit_price=1)
        Invoice.objects.create(customer=self.customer, opportunity=opportunity, costing_header=source, invoice_number="ADV-INV", total_amount=100, paid_amount=100)
        self.assertTrue(approve_production_release(opportunity, user=self.ceo)["ready"])
        CostingLineItem.objects.filter(pk=line.pk).update(unit_price=2)
        self.assertFalse(production_readiness(opportunity)["production_approved"])

    def test_advanced_invoice_conversion_allows_unpaid_invoice_without_500(self):
        opportunity = Opportunity.objects.create(customer=self.customer)
        source = CostingHeader.objects.create(
            opportunity=opportunity, customer=self.customer, status="approved",
            approved_by=self.ceo, approved_at=timezone.now(), quotation_number="ADV-BLOCK",
            quoted_by=self.ceo, quoted_at=timezone.now(), quotation_status="approved",
            quotation_approved_by=self.ceo, quotation_approved_at=timezone.now(),
            currency="CAD", order_quantity=10, manual_fob_per_piece=10,
        )
        CostingLineItem.objects.create(costing=source, item_name="Fabric", unit_price=1, consumption_value=1)
        invoice = Invoice.objects.create(customer=self.customer, opportunity=opportunity, costing_header=source, invoice_number="ADV-BLOCK-INV", total_amount=100, paid_amount=0)
        self.client.force_login(self.ceo)
        response = self.client.post(reverse("invoice_convert_to_production_order", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("required customer deposit must be verified", " ".join(str(item) for item in get_messages(response.wsgi_request)))
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 0)

    def test_production_employee_repeated_release_does_not_duplicate_order(self):
        opportunity, _, _ = self.source()
        order = ProductionOrder.objects.create(title="Existing approved production", opportunity=opportunity)
        actor = get_user_model().objects.create_user(username="release-production-only")
        actor.groups.add(Group.objects.get_or_create(name="Production")[0])
        self.client.force_login(actor)
        response = self.client.post(reverse("production_from_opportunity", args=[opportunity.pk]), {"action": "release"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("production_detail", args=[order.pk]))
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)

    def test_production_employee_can_release_ready_sample_and_bulk_without_approving_terms(self):
        from crm.services.production_orders import create_production_order_from_paid_full_package_quick_costing
        from crm.services.operations_permissions import can_approve_costing

        actor = get_user_model().objects.create_user(username="authorized-production")
        actor.groups.add(Group.objects.get_or_create(name="Production")[0])
        self.assertFalse(can_approve_costing(actor))
        for sample, paid in ((True, "0"), (True, "20"), (True, "100"), (False, "0"), (False, "20"), (False, "37.5")):
            with self.subTest(sample=sample, paid=paid):
                opportunity, quick, invoice = self.source(sample=sample, paid=paid, percentage="37.5")
                ready = production_readiness(opportunity, user=actor)
                self.assertTrue(ready["ready"])
                self.assertTrue(ready["can_release"])
                self.assertFalse(ready["can_exception"])
                self.assertIsNone(ready["release_approval"])
                order, created = create_production_order_from_paid_full_package_quick_costing(quick, invoice=invoice, user=actor)
                self.assertTrue(created)
                self.assertEqual(order.created_by_id, actor.pk)
                self.assertFalse(CRMAuditLog.objects.filter(field_name=APPROVAL_FIELD).exists())

    def test_production_permission_does_not_waive_commercial_requirements(self):
        actor = get_user_model().objects.create_user(username="production-no-exception")
        actor.groups.add(Group.objects.get_or_create(name="Production")[0])
        opportunity, quick, invoice = self.source(sample=True, paid="0")
        QuickCosting.objects.filter(pk=quick.pk).update(quotation_number="", quoted_at=None)
        with self.assertRaisesRegex(ProductionApprovalError, "Quotation approval"):
            assert_production_release(opportunity, source=quick, invoice=invoice, user=actor)
        with self.assertRaises(ProductionApprovalError):
            approve_production_release(opportunity, user=actor, reason="Attempted waiver", waived_requirements=["quotation"])
        self.assertFalse(ProductionOrder.objects.exists())

    def test_release_post_preserves_unverified_payment_and_financial_records(self):
        opportunity, quick, invoice = self.source(sample=True, paid="0")
        actor = get_user_model().objects.create_user(username="unpaid-production-release")
        actor.groups.add(Group.objects.get_or_create(name="Production")[0])
        operation = FinanceOperation.objects.create(
            operation_number="UNVERIFIED-RELEASE", operation_type=FinanceOperation.TYPE_CUSTOMER_PAYMENT,
            state=FinanceOperation.STATE_PENDING, transaction_date=timezone.localdate(),
            invoice=invoice, opportunity=opportunity, customer=self.customer,
            side="CA", currency="CAD", total_amount=100, amount_before_tax=100,
            rate_to_cad=1, rate_to_bdt=100, amount_cad=100, amount_bdt=10000,
            created_by=self.employee, payment_method="bank_transfer",
        )
        financial_models = (
            InvoicePayment, FinanceOperation, FinancialAuditEvent, InvoiceFinancialState,
            JournalEntry, JournalLine, AccountingEntry,
        )
        before = {model.__name__: list(model.objects.order_by("pk").values()) for model in financial_models}
        invoice_fields = [field.attname for field in Invoice._meta.concrete_fields if field.name not in {"order", "updated_at"}]
        invoice_before = Invoice.objects.values(*invoice_fields).get(pk=invoice.pk)
        approvals_before = (quick.approved_by_id, quick.approved_at, quick.quoted_by_id, quick.quoted_at)
        self.assertFalse(production_readiness(opportunity)["payment_verified"])
        self.assertFalse(ProductionOrder.objects.exists())
        self.client.force_login(actor)
        started = timezone.now()
        response = self.client.post(reverse("production_from_opportunity", args=[opportunity.pk]), {"action": "release"})
        self.assertEqual(response.status_code, 302)
        order = ProductionOrder.objects.get(opportunity=opportunity)
        self.assertEqual(response.url, reverse("production_detail", args=[order.pk]))
        self.assertEqual(order.created_by_id, actor.pk)
        self.assertEqual(order.opportunity_id, opportunity.pk)
        self.assertGreaterEqual(order.created_at, started)
        self.assertLessEqual(order.created_at, timezone.now())
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, "Production")
        self.client.post(reverse("production_from_opportunity", args=[opportunity.pk]), {"action": "release"})
        self.assertEqual(ProductionOrder.objects.filter(opportunity=opportunity).count(), 1)
        self.assertEqual(before, {model.__name__: list(model.objects.order_by("pk").values()) for model in financial_models})
        self.assertEqual(invoice_before, Invoice.objects.values(*invoice_fields).get(pk=invoice.pk))
        invoice.refresh_from_db()
        operation.refresh_from_db()
        quick.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 0)
        self.assertEqual(invoice.balance, 100)
        self.assertEqual(operation.state, FinanceOperation.STATE_PENDING)
        self.assertIsNone(operation.approved_by_id)
        self.assertEqual(approvals_before, (quick.approved_by_id, quick.approved_at, quick.quoted_by_id, quick.quoted_at))

    def test_release_post_still_requires_costing_and_quotation_approval(self):
        actor = get_user_model().objects.create_user(username="approval-enforced-production")
        actor.groups.add(Group.objects.get_or_create(name="Production")[0])
        self.client.force_login(actor)
        for missing in ("costing", "quotation"):
            with self.subTest(missing=missing):
                opportunity, quick, _ = self.source(paid="0")
                if missing == "costing":
                    QuickCosting.objects.filter(pk=quick.pk).update(approved_at=None, approved_by=None)
                else:
                    QuickCosting.objects.filter(pk=quick.pk).update(quoted_at=None, quotation_number="")
                response = self.client.post(reverse("production_from_opportunity", args=[opportunity.pk]), {"action": "release"})
                self.assertEqual(response.status_code, 302)
                self.assertIn(f"{missing.title()} approval is pending", " ".join(str(item) for item in get_messages(response.wsgi_request)))
                self.assertFalse(ProductionOrder.objects.filter(opportunity=opportunity).exists())

    def test_sales_and_finance_cannot_release_ready_orders_without_production_permission(self):
        opportunity, quick, invoice = self.source(sample=True)
        for role in ("Sales", "Finance"):
            actor = get_user_model().objects.create_user(username=f"no-production-{role}")
            actor.groups.add(Group.objects.get_or_create(name=role)[0])
            with self.subTest(role=role), self.assertRaisesRegex(ProductionApprovalError, "Production access"):
                assert_production_release(opportunity, source=quick, invoice=invoice, user=actor)
        self.assertFalse(ProductionOrder.objects.exists())

    def test_manual_production_add_cannot_bypass_approval_by_omitting_opportunity(self):
        actor = get_user_model().objects.create_user(username="manual-production-bypass")
        actor.groups.add(Group.objects.get_or_create(name="Production")[0])
        self.client.force_login(actor)
        response = self.client.post(reverse("production_add"), {
            "title": "Unapproved customer sample", "customer": self.customer.pk,
            "factory_location": "bd", "order_type": "fob", "production_order_type": "sampling",
            "operational_status": "planning", "qty_total": 2, "completed_quantity": 0,
            "qty_reject": 0, "opportunity": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductionOrder.objects.exists())
