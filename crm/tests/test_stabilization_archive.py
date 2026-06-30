from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from crm.forms import LeadForm
from crm.models import AccountingEntry, CRMAuditLog, Invoice, InvoicePayment, Lead
from crm.services.employee_identity import resolve_lead_owner
from crm.services.operations_search import search_operations_records


class EmployeeArchiveStabilizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.ceo = User.objects.create_user("archive-ceo", first_name="CEO")
        cls.ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        cls.admin = User.objects.create_user("archive-admin", first_name="Admin")
        cls.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        cls.normal = User.objects.create_user("archive-normal", first_name="Normal")

    def _employee(self, suffix):
        return get_user_model().objects.create_user(
            f"employee-{suffix}", first_name="Historical", last_name=suffix.title()
        )

    def test_ceo_and_admin_archive_without_deleting_employee(self):
        for actor, suffix in ((self.ceo, "ceo"), (self.admin, "admin")):
            with self.subTest(actor=actor.username):
                target = self._employee(suffix)
                employee_id = target.employee_profile.employee_id
                self.client.force_login(actor)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(reverse("employee_archive", args=[target.pk]))
                self.assertRedirects(response, reverse("employee_list"))
                target.refresh_from_db()
                target.employee_profile.refresh_from_db()
                self.assertFalse(target.is_active)
                self.assertTrue(target.employee_profile.is_archived)
                self.assertEqual(target.employee_profile.employee_id, employee_id)
                self.assertTrue(get_user_model().objects.filter(pk=target.pk).exists())
                self.assertTrue(
                    CRMAuditLog.objects.filter(
                        module="employees",
                        record_id=str(target.pk),
                        field_name="archived",
                        new_value="Archived",
                    ).exists()
                )

    def test_normal_user_cannot_archive_or_view_archived_directory(self):
        target = self._employee("blocked")
        self.client.force_login(self.normal)
        self.assertEqual(
            self.client.post(reverse("employee_archive", args=[target.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("employee_list"), {"archive": "archived"}).status_code,
            403,
        )
        target.employee_profile.refresh_from_db()
        self.assertFalse(target.employee_profile.is_archived)

    def test_archive_hides_employee_but_preserves_history_and_identity(self):
        target = self._employee("history")
        target.employee_profile.display_name = "Historical Owner"
        target.employee_profile.save(update_fields=["display_name"])
        lead = Lead.objects.create(
            account_brand="Retained History",
            owner="Original owner text",
            assigned_to=target,
        )
        self.client.force_login(self.ceo)
        self.client.post(reverse("employee_archive", args=[target.pk]))

        active = self.client.get(reverse("employee_list"))
        archived = self.client.get(reverse("employee_list"), {"archive": "archived"})
        self.assertNotIn(target.pk, [row.user_id for row in active.context["profiles"]])
        self.assertIn(target.pk, [row.user_id for row in archived.context["profiles"]])
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to_id, target.pk)
        self.assertEqual(lead.owner, "Original owner text")
        self.assertEqual(resolve_lead_owner(lead)["canonical_name"], "Historical Owner")

        self.assertNotIn(
            target.pk,
            LeadForm().fields["assigned_to"].queryset.values_list("pk", flat=True),
        )
        employee_results = [
            row
            for label, rows in search_operations_records(self.ceo, "Historical Owner")
            if label == "Employees"
            for row in rows
        ]
        self.assertEqual(employee_results, [])

    def test_restore_preserves_id_and_does_not_enable_login(self):
        target = self._employee("restore")
        employee_id = target.employee_profile.employee_id
        self.client.force_login(self.ceo)
        self.client.post(reverse("employee_archive", args=[target.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("employee_restore", args=[target.pk]))
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        target.employee_profile.refresh_from_db()
        self.assertFalse(target.employee_profile.is_archived)
        self.assertFalse(target.is_active)
        self.assertEqual(target.employee_profile.employee_id, employee_id)


class InvoiceArchiveStabilizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.ceo = User.objects.create_user("invoice-archive-ceo")
        cls.ceo.groups.add(Group.objects.get_or_create(name="CEO")[0])
        cls.admin = User.objects.create_user("invoice-archive-admin")
        cls.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        cls.accounts_manager = User.objects.create_user("invoice-archive-manager")
        cls.accounts_manager.groups.add(Group.objects.get_or_create(name="Accounts")[0])
        cls.accounts_manager.employee_profile.position = "accounts_manager"
        cls.accounts_manager.employee_profile.save(update_fields=["position"])
        cls.accounts_officer = User.objects.create_user("invoice-archive-officer")
        cls.accounts_officer.groups.add(Group.objects.get_or_create(name="Accounts")[0])
        cls.accounts_officer.employee_profile.position = "accounts_executive"
        cls.accounts_officer.employee_profile.save(update_fields=["position"])
        cls.normal = User.objects.create_user("invoice-archive-normal")

    def _invoice(self, suffix, paid=Decimal("0")):
        return Invoice.objects.create(
            invoice_number=f"INV-ARCHIVE-{suffix}",
            currency="CAD",
            subtotal=Decimal("100"),
            total_amount=Decimal("100"),
            paid_amount=paid,
            status="partial" if paid else "sent",
        )

    def test_authorized_roles_archive_unpaid_invoice_with_audit(self):
        for actor, suffix in (
            (self.ceo, "CEO"),
            (self.admin, "ADMIN"),
            (self.accounts_manager, "MANAGER"),
        ):
            with self.subTest(actor=actor.username):
                invoice = self._invoice(suffix)
                self.client.force_login(actor)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(reverse("invoice_archive", args=[invoice.pk]))
                self.assertEqual(response.status_code, 302)
                invoice.refresh_from_db()
                self.assertTrue(invoice.is_archived)
                self.assertEqual(invoice.archived_by_id, actor.pk)
                self.assertTrue(
                    CRMAuditLog.objects.filter(
                        module="invoices",
                        record_id=str(invoice.pk),
                        field_name="is_archived",
                        new_value="True",
                    ).exists()
                )

    def test_normal_user_cannot_archive_or_view_archived_invoices(self):
        invoice = self._invoice("BLOCKED")
        self.client.force_login(self.normal)
        self.assertEqual(
            self.client.post(reverse("invoice_archive", args=[invoice.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("invoice_list"), {"archive": "archived"}).status_code,
            403,
        )

    def test_any_payment_history_blocks_archive_and_is_preserved(self):
        invoice = self._invoice("PAYMENT")
        payment = InvoicePayment.objects.create(
            invoice=invoice,
            amount=Decimal("10"),
            currency="CAD",
            side="CA",
        )
        self.client.force_login(self.accounts_manager)
        response = self.client.post(reverse("invoice_archive", args=[invoice.pk]), follow=True)
        self.assertContains(response, "This invoice has payments recorded")
        invoice.refresh_from_db()
        self.assertFalse(invoice.is_archived)
        self.assertTrue(InvoicePayment.objects.filter(pk=payment.pk).exists())

    def test_accounting_transaction_link_blocks_archive(self):
        invoice = self._invoice("ACCOUNTING")
        entry = AccountingEntry.objects.create(
            date=invoice.issue_date,
            side="CA",
            direction="IN",
            main_type="INCOME",
            currency="CAD",
            amount_original=Decimal("10"),
        )
        InvoicePayment.objects.create(
            invoice=invoice,
            accounting_entry=entry,
            amount=Decimal("10"),
            currency="CAD",
            side="CA",
        )
        self.client.force_login(self.ceo)
        response = self.client.post(reverse("invoice_archive", args=[invoice.pk]), follow=True)
        self.assertContains(response, "This invoice has payments recorded")
        invoice.refresh_from_db()
        self.assertFalse(invoice.is_archived)

    def test_active_list_hides_archive_and_authorized_search_finds_it(self):
        active = self._invoice("ACTIVE")
        archived = self._invoice("HIDDEN")
        archived.is_archived = True
        archived.save(update_fields=["is_archived"])
        self.client.force_login(self.accounts_manager)

        active_page = self.client.get(reverse("invoice_list"))
        archive_page = self.client.get(reverse("invoice_list"), {"archive": "archived"})
        self.assertContains(active_page, active.invoice_number)
        self.assertNotContains(active_page, archived.invoice_number)
        self.assertContains(archive_page, archived.invoice_number)
        results = [
            row
            for label, rows in search_operations_records(self.accounts_manager, archived.invoice_number)
            if label == "Invoices"
            for row in rows
        ]
        self.assertEqual([row["number"] for row in results], [archived.invoice_number])
        self.assertIn("Archived", results[0]["status"])

    def test_receivables_exclude_archived_by_default_and_can_include(self):
        active = self._invoice("REPORT-ACTIVE")
        archived = self._invoice("REPORT-ARCHIVED")
        archived.is_archived = True
        archived.save(update_fields=["is_archived"])
        self.client.force_login(self.ceo)

        default = self.client.get(reverse("accounts_receivable_dashboard"))
        included = self.client.get(
            reverse("accounts_receivable_dashboard"), {"include_archived": "1"}
        )
        self.assertEqual(default.context["invoice_count"], 1)
        self.assertEqual(included.context["invoice_count"], 2)
        self.assertEqual(default.context["total_balance_due"], active.total_amount)
        self.assertEqual(
            included.context["total_balance_due"],
            active.total_amount + archived.total_amount,
        )

        self.client.force_login(self.accounts_officer)
        unauthorized_include = self.client.get(
            reverse("accounts_receivable_dashboard"), {"include_archived": "1"}
        )
        self.assertEqual(unauthorized_include.context["invoice_count"], 1)
        self.assertFalse(unauthorized_include.context["filter_values"]["include_archived"])
        self.assertFalse(unauthorized_include.context["filter_values"]["can_include_archived"])
