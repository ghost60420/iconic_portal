from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from crm.models import BDStaff, BDStaffMonth


class EmployeeModuleUITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="employee-ui-admin",
            email="employee-ui-admin@example.com",
            password="pass",
        )
        self.client.force_login(self.user)
        self.staff = BDStaff.objects.create(
            name="Amina Begum",
            role="Sewing Operator",
            base_salary_bdt=Decimal("18000.00"),
            is_active=True,
        )
        self.month = BDStaffMonth.objects.create(
            staff=self.staff,
            year=2026,
            month=7,
            base_salary_bdt=Decimal("18000.00"),
            overtime_hours=Decimal("8.00"),
            overtime_rate_bdt=Decimal("150.00"),
            bonus_bdt=Decimal("500.00"),
            deduction_bdt=Decimal("100.00"),
            is_paid=True,
        )

    def test_staff_list_renders_modern_employee_table(self):
        response = self.client.get(reverse("bd_staff_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "crm-modern-bridge")
        self.assertContains(response, "crm-employee-bridge")
        self.assertContains(response, self.staff.name)
        self.assertContains(response, reverse("bd_staff_edit", args=[self.staff.pk]))

    def test_staff_form_preserves_fields_and_csrf(self):
        response = self.client.get(reverse("bd_staff_edit", args=[self.staff.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "employee-modern")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="role"')
        self.assertContains(response, 'name="base_salary_bdt"')
        self.assertContains(response, 'name="is_active"')

    def test_payroll_list_preserves_filter_and_generate_forms(self):
        response = self.client.get(reverse("bd_staff_month_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "crm-modern-bridge")
        self.assertContains(response, "crm-employee-bridge")
        self.assertContains(response, 'id="yearInput"')
        self.assertContains(response, 'id="monthInput"')
        self.assertContains(response, 'id="genYear"')
        self.assertContains(response, 'id="genMonth"')
        self.assertContains(response, reverse("bd_staff_month_generate"))
        self.assertContains(response, reverse("bd_staff_month_edit", args=[self.month.pk]))

    def test_payroll_edit_preserves_fields_and_paid_date_toggle_target(self):
        response = self.client.get(reverse("bd_staff_month_edit", args=[self.month.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "employee-modern")
        self.assertContains(response, 'id="paid-date-wrap"')
        self.assertContains(response, 'name="base_salary_bdt"')
        self.assertContains(response, 'name="overtime_hours"')
        self.assertContains(response, 'name="overtime_rate_bdt"')
        self.assertContains(response, 'name="bonus_bdt"')
        self.assertContains(response, 'name="deduction_bdt"')
        self.assertContains(response, 'name="is_paid"')
        self.assertContains(response, 'name="note"')

    def test_payroll_generate_preserves_required_post_fields(self):
        response = self.client.get(reverse("bd_staff_month_generate"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "employee-modern")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="year"')
        self.assertContains(response, 'name="month"')
        self.assertContains(response, "required")
