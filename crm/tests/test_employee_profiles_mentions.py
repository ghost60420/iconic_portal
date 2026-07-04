from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AutomationNotification,
    CostingHeader,
    CRMAuditLog,
    Customer,
    EmployeeProfile,
    EmployeeIdSequence,
    Invoice,
    Lead,
    LeadActivity,
    LeadComment,
    Opportunity,
    ProductionOrder,
)
from crm.services.chatter_mentions import notify_chatter_mentions
from crm.services.employee_profiles import build_employee_timeline, employee_audit, set_employee_roles
from crm.services.operations_permissions import can_access_operations_module
from crm.permissions import role_flag_decision
from crm.services.sales_profiles import build_salesperson_profile, build_team_performance
from crm.templatetags.crm_people import highlight_mentions


class EmployeeProfileFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        for role in (
            "CEO", "Manager", "Sales", "Merchandising", "Production", "Accounts",
            "Finance", "QC", "Warehouse", "HR", "Admin", "Read Only",
            "Director", "Merchandiser", "Supervisor",
        ):
            Group.objects.get_or_create(name=role)

        cls.hossein = cls.User.objects.create_user(
            "hossein", first_name="Hossain", last_name="Forhad", email="hossein@example.com"
        )
        cls.hossein.groups.add(Group.objects.get(name="CEO"))
        cls.hossein.employee_profile.display_name = "Hossain"
        cls.hossein.employee_profile.position = "ceo"
        cls.hossein.employee_profile.department = "management"
        cls.hossein.employee_profile.save()

        cls.refat = cls.User.objects.create_user("refat", first_name="Refat")
        cls.refat.groups.add(Group.objects.get(name="Sales"))
        cls.refat.employee_profile.display_name = "Refat"
        cls.refat.employee_profile.position = "sales_manager"
        cls.refat.employee_profile.department = "sales"
        cls.refat.employee_profile.save()

        cls.talha = cls.User.objects.create_user("talha", first_name="Talha")
        cls.talha.groups.add(Group.objects.get(name="Sales"))
        cls.talha.employee_profile.display_name = "Talha"
        cls.talha.employee_profile.position = "sales_executive"
        cls.talha.employee_profile.department = "sales"
        cls.talha.employee_profile.save()

        cls.biplob = cls.User.objects.create_user("biplob", first_name="Biplob")
        cls.biplob.groups.add(
            Group.objects.get(name="Merchandising"),
            Group.objects.get(name="Manager"),
            Group.objects.get(name="Accounts"),
        )
        cls.biplob.employee_profile.display_name = "Biplob"
        cls.biplob.employee_profile.position = "merchandising_manager"
        cls.biplob.employee_profile.department = "merchandising"
        cls.biplob.employee_profile.save()

        cls.regular = cls.User.objects.create_user("regular", first_name="Regular")

    def setUp(self):
        self.client = Client()

    def test_profile_is_created_and_display_name_is_correct(self):
        user = self.User.objects.create_user("new-person", first_name="Nadia")
        self.assertEqual(user.employee_profile.display_name, "Nadia")
        self.assertEqual(self.hossein.employee_profile.display_name, "Hossain")
        self.assertNotEqual(self.hossein.employee_profile.display_name, "Hussain")

    def test_talha_is_salesperson_not_production(self):
        self.assertEqual(self.talha.employee_profile.position, "sales_executive")
        self.assertTrue(self.talha.groups.filter(name="Sales").exists())
        self.assertFalse(self.talha.groups.filter(name="Production").exists())

    def test_multiple_roles_are_supported(self):
        self.assertEqual(
            set(self.biplob.groups.values_list("name", flat=True)),
            {"Merchandising", "Manager", "Accounts"},
        )

    def test_ceo_and_admin_can_manage_employees(self):
        self.client.force_login(self.hossein)
        self.assertEqual(self.client.get(reverse("employee_list")).status_code, 200)
        admin = self.User.objects.create_user("people-admin")
        admin.groups.add(Group.objects.get(name="Admin"))
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("employee_list")).status_code, 200)

    def test_ceo_can_create_employee_profile_with_multiple_roles(self):
        self.client.force_login(self.hossein)
        sales = Group.objects.get(name="Sales")
        merchandising = Group.objects.get(name="Merchandising")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("employee_create"),
                {
                    "username": "new-sales-merch",
                    "full_name": "Nadia Rahman",
                    "display_name": "Nadia",
                    "email": "nadia@example.com",
                    "employee_id": "SHOULD-NOT-BE-USED",
                    "position": "sales_executive",
                    "department": "sales",
                    "status": "active",
                    "is_active": "on",
                    "roles": [sales.pk, merchandising.pk],
                },
            )
        self.assertEqual(response.status_code, 302)
        created = self.User.objects.get(username="new-sales-merch")
        self.assertEqual(created.employee_profile.display_name, "Nadia")
        self.assertRegex(created.employee_profile.employee_id, r"^EMP\d{4,}$")
        self.assertNotEqual(created.employee_profile.employee_id, "SHOULD-NOT-BE-USED")
        self.assertEqual(
            set(created.groups.values_list("name", flat=True)),
            {"Sales", "Merchandising"},
        )
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="employees", record_id=str(created.pk), field_name="profile"
            ).exists()
        )

    def test_regular_user_is_blocked_from_employee_management(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("employee_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("role_management")).status_code, 403)

    def test_profile_and_role_changes_are_audited(self):
        self.client.force_login(self.hossein)
        sales = Group.objects.get(name="Sales")
        manager = Group.objects.get(name="Manager")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("employee_edit", args=[self.talha.pk]),
                {
                    "username": "talha",
                    "full_name": "Talha Ahmed",
                    "display_name": "Talha",
                    "email": "talha@example.com",
                    "phone": "6045550123",
                    "employee_id": "EMP-TA-1",
                    "position": "sales_executive",
                    "department": "sales",
                    "status": "active",
                    "manager": self.refat.pk,
                    "is_active": "on",
                    "roles": [sales.pk, manager.pk],
                    "notes": "Sales team",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.talha.refresh_from_db()
        self.assertEqual(set(self.talha.groups.values_list("name", flat=True)), {"Sales", "Manager"})
        audit_fields = set(
            CRMAuditLog.objects.filter(module="employees", record_id=str(self.talha.pk))
            .values_list("field_name", flat=True)
        )
        self.assertIn("manager", audit_fields)
        self.assertIn("roles", audit_fields)
        self.assertIn("role_added", audit_fields)

    def test_active_state_change_is_audited(self):
        self.client.force_login(self.hossein)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("employee_edit", args=[self.regular.pk]),
                {
                    "username": "regular",
                    "full_name": "Regular User",
                    "display_name": "Regular",
                    "email": "",
                    "phone": "",
                    "employee_id": "",
                    "position": "staff",
                    "department": "administration",
                    "status": "active",
                    "manager": "",
                    "notes": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.regular.refresh_from_db()
        self.assertFalse(self.regular.is_active)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="employees",
                record_id=str(self.regular.pk),
                field_name="active",
                previous_value="Active",
                new_value="Inactive",
            ).exists()
        )

    def test_last_active_ceo_cannot_be_deactivated(self):
        admin = self.User.objects.create_user("last-ceo-admin", first_name="Admin")
        admin.groups.add(Group.objects.get(name="Admin"))
        self.client.force_login(admin)
        ceo = Group.objects.get(name="CEO")
        response = self.client.post(
            reverse("employee_edit", args=[self.hossein.pk]),
            {
                "username": "hossein",
                "full_name": "Hossain Forhad",
                "display_name": "Hossain",
                "email": "hossein@example.com",
                "phone": "",
                "employee_id": "",
                "position": "ceo",
                "department": "management",
                "status": "active",
                "manager": "",
                "roles": [ceo.pk],
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The last active CEO account cannot be deactivated.")
        self.hossein.refresh_from_db()
        self.assertTrue(self.hossein.is_active)

    def test_employee_status_is_audited_and_suspended_user_cannot_sign_in(self):
        self.client.force_login(self.hossein)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("employee_edit", args=[self.regular.pk]),
                {
                    "username": "regular",
                    "full_name": "Regular User",
                    "display_name": "Regular",
                    "email": "",
                    "phone": "",
                    "employee_id": "",
                    "position": "staff",
                    "department": "administration",
                    "status": "suspended",
                    "manager": "",
                    "is_active": "on",
                    "notes": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.regular.refresh_from_db()
        self.regular.employee_profile.refresh_from_db()
        self.assertEqual(self.regular.employee_profile.status, EmployeeProfile.STATUS_SUSPENDED)
        self.assertFalse(self.regular.is_active)
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="employees",
                record_id=str(self.regular.pk),
                field_name="status",
                previous_value="Active",
                new_value="Suspended",
            ).exists()
        )

    def test_employee_user_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.regular.delete()

    def test_employee_numbers_are_permanent_and_never_reused(self):
        first = self.User.objects.create_user("employee-number-one", first_name="One")
        first_number = first.employee_profile.employee_id
        first.employee_profile.status = EmployeeProfile.STATUS_RESIGNED
        first.employee_profile.save(update_fields=["status"])
        with self.assertRaises(ProtectedError):
            first.delete()
        second = self.User.objects.create_user("employee-number-two", first_name="Two")
        self.assertRegex(first_number, r"^EMP\d{4,}$")
        self.assertRegex(second.employee_profile.employee_id, r"^EMP\d{4,}$")
        self.assertGreater(int(second.employee_profile.employee_id[3:]), int(first_number[3:]))
        self.assertEqual(EmployeeIdSequence.objects.get(pk="employee").last_value, int(second.employee_profile.employee_id[3:]))

    def test_position_and_department_lists_are_standardized(self):
        self.assertEqual(
            [label for _value, label in EmployeeProfile.POSITION_CHOICES],
            [
                "CEO", "Director", "General Manager", "Operations Manager", "Sales Manager",
                "Production Manager", "Merchandising Manager", "Accounts Manager", "Sales Executive",
                "Merchandiser", "Production Coordinator", "Quality Controller", "Accountant",
                "Customer Service", "Administrator", "Staff", "Other",
            ],
        )
        self.assertEqual(
            [label for _value, label in EmployeeProfile.DEPARTMENT_CHOICES],
            [
                "Management", "Sales", "Merchandising", "Production", "Accounts", "Administration",
                "Quality Control", "Logistics", "IT", "Marketing", "Customer Service",
            ],
        )

    def test_inactive_status_disables_login_without_deleting_history(self):
        self.client.force_login(self.hossein)
        response = self.client.post(
            reverse("employee_edit", args=[self.regular.pk]),
            {
                "username": "regular", "full_name": "Regular User", "display_name": "Regular",
                "email": "", "phone": "", "position": "staff", "department": "administration",
                "status": "inactive", "manager": "", "is_active": "on", "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.regular.refresh_from_db()
        self.regular.employee_profile.refresh_from_db()
        self.assertFalse(self.regular.is_active)
        self.assertEqual(self.regular.employee_profile.status, EmployeeProfile.STATUS_INACTIVE)
        self.assertTrue(self.regular.employee_profile.employee_id)

    def test_manager_hierarchy_rejects_cycles(self):
        self.refat.employee_profile.manager = self.talha
        self.refat.employee_profile.save(update_fields=["manager"])
        self.talha.employee_profile.manager = self.refat
        with self.assertRaises(ValidationError):
            self.talha.employee_profile.save(update_fields=["manager"])

    def test_employee_timeline_contains_profile_events_and_last_login(self):
        self.talha.last_login = timezone.now()
        self.talha.save(update_fields=["last_login"])
        with self.captureOnCommitCallbacks(execute=True):
            employee_audit(self.hossein, self.talha, "department", "Sales", "Management")
        events = build_employee_timeline(self.talha.employee_profile)
        titles = {event["title"] for event in events}
        self.assertIn("Created", titles)
        self.assertIn("Department changed", titles)
        self.assertIn("Last login", titles)

    def test_password_reset_appears_in_employee_timeline_without_password_data(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.talha.set_password("Local-test-password-4821")
            self.talha.save(update_fields=["password"])
        audit = CRMAuditLog.objects.get(
            module="employees",
            record_id=str(self.talha.pk),
            field_name="password_reset",
        )
        self.assertEqual(audit.previous_value, "")
        self.assertEqual(audit.new_value, "Password reset")
        self.assertIn("Password reset", {event["title"] for event in build_employee_timeline(self.talha.employee_profile)})

    def test_manager_selector_uses_display_name_and_position(self):
        self.client.force_login(self.hossein)
        response = self.client.get(reverse("employee_edit", args=[self.talha.pk]))
        self.assertContains(response, "Refat — Sales Manager")

    def test_role_removal_has_a_specific_audit_entry(self):
        remaining_roles = list(self.biplob.groups.exclude(name="Accounts"))
        with self.captureOnCommitCallbacks(execute=True):
            set_employee_roles(
                actor=self.hossein,
                target_user=self.biplob,
                selected_roles=remaining_roles,
            )
        self.assertTrue(
            CRMAuditLog.objects.filter(
                module="employees",
                record_id=str(self.biplob.pk),
                field_name="role_removed",
                previous_value="Accounts",
            ).exists()
        )

    def test_employee_and_role_pages_have_bounded_queries(self):
        self.client.force_login(self.hossein)
        with CaptureQueriesContext(connection) as employee_queries:
            employee_response = self.client.get(reverse("employee_list"))
        with CaptureQueriesContext(connection) as role_queries:
            role_response = self.client.get(reverse("role_management"))
        self.assertEqual(employee_response.status_code, 200)
        self.assertEqual(role_response.status_code, 200)
        self.assertLessEqual(len(employee_queries), 15)
        self.assertLessEqual(len(role_queries), 22)

    def test_employee_directory_search_filter_and_sort(self):
        self.client.force_login(self.hossein)
        response = self.client.get(reverse("employee_list"), {"q": "Merchandising"})
        self.assertEqual([profile.public_name for profile in response.context["profiles"]], ["Biplob"])

        self.biplob.employee_profile.status = EmployeeProfile.STATUS_ON_LEAVE
        self.biplob.employee_profile.save(update_fields=["status"])
        response = self.client.get(reverse("employee_list"), {"status": "on_leave"})
        self.assertContains(response, "Biplob")
        self.assertNotContains(response, "Hossain Forhad")

        response = self.client.get(
            reverse("employee_list"),
            {"sort": "employee_id", "direction": "desc"},
        )
        employee_ids = [profile.employee_id for profile in response.context["profiles"]]
        self.assertEqual(employee_ids, sorted(employee_ids, reverse=True))

    def test_employee_directory_searches_email_and_status(self):
        self.client.force_login(self.hossein)
        self.assertContains(
            self.client.get(reverse("employee_list"), {"q": "hossein@example.com"}),
            "Hossain",
        )
        self.assertContains(
            self.client.get(reverse("employee_list"), {"q": "Active"}),
            "Talha",
        )

    def test_employee_profile_card_statistics_and_management_tree(self):
        self.refat.employee_profile.manager = self.hossein
        self.refat.employee_profile.save(update_fields=["manager"])
        self.talha.employee_profile.manager = self.refat
        self.talha.employee_profile.save(update_fields=["manager"])
        self.client.force_login(self.hossein)
        response = self.client.get(reverse("employee_edit", args=[self.talha.pk]))
        self.assertContains(response, self.talha.employee_profile.employee_id)
        self.assertContains(response, "Full Name")
        self.assertContains(response, "Date Joined")
        self.assertContains(response, "Open Opportunities")
        self.assertContains(response, "Management Tree")
        self.assertContains(response, "Hossain")
        self.assertContains(response, "Refat")
        self.assertContains(response, "Talha")

    def test_read_only_role_blocks_writes_and_exports(self):
        hr = self.User.objects.create_user("readonly-hr")
        hr.groups.add(Group.objects.get(name="HR"), Group.objects.get(name="Read Only"))
        self.client.force_login(hr)
        self.assertEqual(
            self.client.post(reverse("employee_edit", args=[self.talha.pk]), {}).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("crm_audit_log"), {"export": "csv"}).status_code,
            403,
        )

    def test_role_setup_command_is_additive(self):
        call_command("setup_operations_roles")
        self.assertTrue(Group.objects.filter(name="Finance").exists())
        self.assertTrue(Group.objects.filter(name="Read Only").exists())
        self.assertTrue(Group.objects.filter(name="Director").exists())
        self.assertTrue(Group.objects.filter(name="Merchandiser").exists())
        self.assertTrue(Group.objects.filter(name="Supervisor").exists())
        self.assertTrue(self.biplob.groups.filter(name="Accounts").exists())

    @patch("crm.services.employee_profiles.CRMAuditLog.objects.bulk_create", side_effect=RuntimeError("audit unavailable"))
    def test_audit_failure_does_not_break_employee_operation(self, _bulk_create):
        with self.assertLogs("django.test", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                employee_audit(self.hossein, self.talha, "position", "Salesperson", "Sales Manager")

    def test_director_access_excludes_ceo_only_settings(self):
        director = self.User.objects.create_user("director-user")
        director.groups.add(Group.objects.get(name="Director"))
        self.assertTrue(can_access_operations_module(director, "finance"))
        self.assertFalse(role_flag_decision(director, "can_view_ceo_tools"))

    def test_manager_sales_visibility_is_limited_to_own_department(self):
        self.client.force_login(self.biplob)
        self.assertEqual(
            self.client.get(reverse("salesperson_profile_user", args=[self.talha.pk])).status_code,
            403,
        )


class ChatterMentionFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.sales_group = Group.objects.get_or_create(name="Sales")[0]
        cls.ceo_group = Group.objects.get_or_create(name="CEO")[0]
        cls.sender = cls.User.objects.create_user("refat", first_name="Refat")
        cls.sender.groups.add(cls.sales_group)
        cls.sender.employee_profile.display_name = "Refat"
        cls.sender.employee_profile.position = "sales_executive"
        cls.sender.employee_profile.department = "sales"
        cls.sender.employee_profile.save()
        cls.recipient = cls.User.objects.create_user("hossein", first_name="Hossain")
        cls.recipient.groups.add(cls.ceo_group)
        cls.recipient.employee_profile.display_name = "Hossain"
        cls.recipient.employee_profile.position = "ceo"
        cls.recipient.employee_profile.department = "management"
        cls.recipient.employee_profile.save()
        cls.inactive = cls.User.objects.create_user("talha", first_name="Talha", is_active=False)
        cls.inactive.employee_profile.display_name = "Talha"
        cls.inactive.employee_profile.save()
        cls.suspended = cls.User.objects.create_user("biplob", first_name="Biplob")
        cls.suspended.employee_profile.display_name = "Biplob"
        cls.suspended.employee_profile.status = EmployeeProfile.STATUS_SUSPENDED
        cls.suspended.employee_profile.save()
        cls.customer = Customer.objects.create(account_brand="Mention Customer")
        cls.lead = Lead.objects.create(account_brand="Mention Customer", customer=cls.customer, assigned_to=cls.sender)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.sender)

    def test_suggestions_use_display_name_and_hide_inactive_users(self):
        response = self.client.get(reverse("mention_suggestions"), {"q": "Ho"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["display_name"], "Hossain")
        self.assertEqual(response.json()["results"][0]["position"], "CEO")
        self.assertEqual(response.json()["results"][0]["initials"], "H")
        self.assertIn("photo_url", response.json()["results"][0])
        inactive = self.client.get(reverse("mention_suggestions"), {"q": "Ta"})
        self.assertEqual(inactive.json()["results"], [])
        suspended = self.client.get(reverse("mention_suggestions"), {"q": "Bi"})
        self.assertEqual(suspended.json()["results"], [])

    def test_suggestions_rank_prefix_before_contains_and_limit_results(self):
        contains = self.User.objects.create_user("contains-ho", first_name="Echo")
        contains.employee_profile.display_name = "Echo"
        contains.employee_profile.save()
        response = self.client.get(reverse("mention_suggestions"), {"q": "Ho"})
        names = [row["display_name"] for row in response.json()["results"]]
        self.assertEqual(names[0], "Hossain")
        self.assertLess(names.index("Hossain"), names.index("Echo"))
        self.assertLessEqual(len(names), 10)

    def test_chatter_identity_uses_display_name_position_and_indicators(self):
        comment = LeadComment.objects.create(
            lead=self.lead,
            author="refat@example.com",
            author_user=self.sender,
            content="@Hossain update",
            pinned=True,
        )
        LeadComment.objects.filter(pk=comment.pk).update(created_at=timezone.now() - timedelta(minutes=2))
        comment.refresh_from_db()
        comment.content = "@Hossain edited update"
        comment.save(update_fields=["content", "updated_at"])
        response = self.client.get(reverse("chatter_feed"))
        self.assertContains(response, "Refat")
        self.assertContains(response, "Sales Executive")
        self.assertContains(response, "Edited")
        self.assertContains(response, "Pinned")
        self.assertContains(response, "✎ Edited")
        self.assertContains(response, "📌 Pinned")
        self.assertNotContains(response, "refat@example.com")

    @patch("django.core.mail.send_mail")
    def test_new_mention_creates_one_crm_notification_and_no_email(self, send_mail):
        response = self.client.post(
            reverse("chatter_feed"),
            {"action": "add_chatter", "comment_text": "@Hossain please review", "link_type": "lead", "link_id": self.lead.pk},
        )
        self.assertEqual(response.status_code, 302)
        comment = LeadComment.objects.get(content__contains="@Hossain")
        item = AutomationNotification.objects.get(source_key=f"chatter-mention:{comment.pk}:user:{self.recipient.pk}")
        self.assertEqual(item.assigned_user, self.recipient)
        self.assertEqual(item.notification_type, "mention")
        self.assertEqual(item.target_url, reverse("lead_detail", args=[self.lead.pk]))
        self.assertFalse(item.is_read)
        notify_chatter_mentions(comment, self.sender)
        self.assertEqual(AutomationNotification.objects.filter(source_key=item.source_key).count(), 1)
        send_mail.assert_not_called()

    def test_sender_is_not_notified(self):
        comment = LeadComment.objects.create(
            lead=self.lead,
            author="Refat",
            author_user=self.sender,
            content="@Refat reminder to myself",
        )
        self.assertEqual(notify_chatter_mentions(comment, self.sender), 0)
        self.assertFalse(AutomationNotification.objects.filter(source_key__startswith=f"chatter-mention:{comment.pk}:").exists())

    def test_role_mention_notifies_active_group_members_once(self):
        colleague = self.User.objects.create_user("sales-colleague", first_name="Nadia")
        colleague.groups.add(self.sales_group)
        colleague.employee_profile.display_name = "Nadia"
        colleague.employee_profile.save()
        comment = LeadComment.objects.create(
            author="Refat",
            author_user=self.sender,
            content="@Sales weekly pipeline reminder",
        )
        self.assertEqual(notify_chatter_mentions(comment, self.sender), 1)
        notification = AutomationNotification.objects.get(
            source_key=f"chatter-mention:{comment.pk}:user:{colleague.pk}"
        )
        self.assertEqual(notification.assigned_user, colleague)
        self.assertEqual(notification.target_url, reverse("chatter_feed"))
        notify_chatter_mentions(comment, self.sender)
        self.assertEqual(
            AutomationNotification.objects.filter(source_key=notification.source_key).count(),
            1,
        )

    def test_chatter_feed_hides_another_salespersons_lead(self):
        other = self.User.objects.create_user("other-sales", first_name="Other")
        other.groups.add(self.sales_group)
        hidden_lead = Lead.objects.create(account_brand="Hidden Brand", assigned_to=other)
        LeadComment.objects.create(lead=hidden_lead, author="Other", author_user=other, content="Private sales note")
        response = self.client.get(reverse("chatter_feed"))
        self.assertNotContains(response, "Private sales note")

    def test_chatter_post_to_unauthorized_record_returns_403(self):
        other = self.User.objects.create_user("post-other-sales", first_name="Other")
        other.groups.add(self.sales_group)
        hidden_lead = Lead.objects.create(account_brand="No Post Brand", assigned_to=other)
        response = self.client.post(
            reverse("chatter_feed"),
            {
                "action": "add_chatter",
                "comment_text": "Unauthorized note",
                "link_type": "lead",
                "link_id": hidden_lead.pk,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(LeadComment.objects.filter(lead=hidden_lead, content="Unauthorized note").exists())

        detail_response = self.client.post(
            reverse("lead_detail", args=[hidden_lead.pk]),
            {"action": "add_comment", "comment_text": "Unauthorized detail note"},
        )
        self.assertEqual(detail_response.status_code, 403)
        self.assertFalse(
            LeadComment.objects.filter(lead=hidden_lead, content="Unauthorized detail note").exists()
        )

    def test_mention_recipient_without_record_access_is_excluded(self):
        other = self.User.objects.create_user("mention-other", first_name="Nadia")
        other.groups.add(self.sales_group)
        other.employee_profile.display_name = "Nadia"
        other.employee_profile.save()
        comment = LeadComment.objects.create(
            lead=self.lead,
            author="Refat",
            author_user=self.sender,
            content="@Nadia please check this",
        )
        self.assertEqual(notify_chatter_mentions(comment, self.sender), 0)
        self.assertFalse(
            AutomationNotification.objects.filter(
                source_key=f"chatter-mention:{comment.pk}:user:{other.pk}"
            ).exists()
        )

    def test_suspended_user_does_not_receive_mention_notification(self):
        comment = LeadComment.objects.create(
            author="Refat",
            author_user=self.sender,
            content="@Biplob internal update",
        )
        self.assertEqual(notify_chatter_mentions(comment, self.sender), 0)
        self.assertFalse(
            AutomationNotification.objects.filter(
                source_key=f"chatter-mention:{comment.pk}:user:{self.suspended.pk}"
            ).exists()
        )

    def test_unauthorized_module_filter_returns_403(self):
        regular = self.User.objects.create_user("chatter-regular")
        regular.groups.add(Group.objects.get_or_create(name="HR")[0])
        self.client.force_login(regular)
        self.assertEqual(
            self.client.get(reverse("chatter_feed"), {"source": "production"}).status_code,
            403,
        )

    def test_mention_highlighting_escapes_message_html(self):
        rendered = str(highlight_mentions("<script>alert(1)</script> @Hossain"))
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn('<span class="crm-mention">@Hossain</span>', rendered)

    def test_mention_suggestions_have_bounded_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("mention_suggestions"), {"q": "Ho"})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 8)


class SalespersonDashboardFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.sales_group = Group.objects.get_or_create(name="Sales")[0]
        cls.manager_group = Group.objects.get_or_create(name="Manager")[0]
        cls.sales = cls.User.objects.create_user("sales-metrics", first_name="Talha")
        cls.sales.groups.add(cls.sales_group)
        cls.sales.employee_profile.display_name = "Talha"
        cls.sales.employee_profile.position = "sales_executive"
        cls.sales.employee_profile.department = "sales"
        cls.sales.employee_profile.save()
        cls.other_sales = cls.User.objects.create_user("other-metrics", first_name="Other")
        cls.other_sales.groups.add(cls.sales_group)
        cls.other_sales.employee_profile.department = "sales"
        cls.other_sales.employee_profile.save()
        cls.manager = cls.User.objects.create_user("sales-manager", first_name="Refat")
        cls.manager.groups.add(cls.manager_group)
        cls.manager.employee_profile.department = "sales"
        cls.manager.employee_profile.save()
        cls.regular = cls.User.objects.create_user("profile-regular")
        cls.today = timezone.localdate()
        cls.customer = Customer.objects.create(account_brand="Metrics Customer")
        cls.open_lead = Lead.objects.create(
            customer=cls.customer,
            account_brand="Open Lead",
            assigned_to=cls.sales,
            next_followup=cls.today,
        )
        cls.overdue_lead = Lead.objects.create(
            account_brand="Overdue Lead",
            assigned_to=cls.sales,
            next_follow_up_date=cls.today - timedelta(days=2),
        )
        cls.converted_lead = Lead.objects.create(
            account_brand="Converted Lead", assigned_to=cls.sales, lead_status="Converted"
        )
        cls.opportunity_lead = Lead.objects.create(
            account_brand="Opportunity Lead", assigned_to=cls.sales, lead_status="Converted"
        )
        cls.lost_lead = Lead.objects.create(
            account_brand="Lost Lead", assigned_to=cls.sales, lead_status="Lost"
        )
        cls.open_opportunity = Opportunity.objects.create(
            lead=cls.opportunity_lead,
            customer=cls.customer,
            stage="Proposal",
            order_currency="CAD",
            order_value=Decimal("2000"),
        )
        cls.won_cad = Opportunity.objects.create(
            lead=cls.converted_lead,
            customer=cls.customer,
            stage="Closed Won",
            is_open=False,
            order_currency="CAD",
            order_value=Decimal("121000"),
            order_value_usd=Decimal("1000"),
        )
        cls.won_usd = Opportunity.objects.create(
            lead=cls.converted_lead,
            customer=cls.customer,
            stage="Closed Won",
            is_open=False,
            order_currency="USD",
            order_value=Decimal("500"),
        )
        Opportunity.objects.create(
            lead=cls.lost_lead,
            stage="Closed Lost",
            is_open=False,
            order_currency="CAD",
            order_value=Decimal("750"),
        )
        CostingHeader.objects.create(
            opportunity=cls.open_opportunity,
            customer=cls.customer,
            quotation_number="QT-OPEN",
            quotation_status=CostingHeader.QUOTATION_STATUS_DRAFT,
            quoted_by=cls.sales,
        )
        CostingHeader.objects.create(
            opportunity=cls.won_cad,
            customer=cls.customer,
            quotation_number="QT-APPROVED",
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quoted_by=cls.sales,
        )
        cls.order = ProductionOrder.objects.create(
            title="Metrics Order",
            lead=cls.converted_lead,
            opportunity=cls.won_cad,
            customer=cls.customer,
        )
        for currency, total, paid in (
            ("CAD", Decimal("800"), Decimal("300")),
            ("USD", Decimal("600"), Decimal("100")),
            ("BDT", Decimal("10000"), Decimal("5000")),
        ):
            Invoice.objects.create(
                invoice_number=f"INV-METRIC-{currency}",
                order=cls.order,
                customer=cls.customer,
                currency=currency,
                total_amount=total,
                paid_amount=paid,
                status="paid",
            )

    def setUp(self):
        self.client = Client()

    def test_salesperson_can_view_only_own_profile(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse("salesperson_profile")).status_code, 200)
        self.assertEqual(self.client.get(reverse("salesperson_profile_user", args=[self.other_sales.pk])).status_code, 403)

    def test_manager_can_view_all_sales_profiles(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("salesperson_profile_user", args=[self.sales.pk])).status_code, 200)

    def test_manager_can_view_inactive_salesperson_history(self):
        self.sales.is_active = False
        self.sales.save(update_fields=["is_active"])
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse("salesperson_profile_user", args=[self.sales.pk])).status_code,
            200,
        )

    def test_regular_user_is_blocked(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("salesperson_profile")).status_code, 403)

    def test_metrics_and_currency_values_are_separate(self):
        metrics = build_salesperson_profile(self.sales)
        self.assertEqual(metrics["lead_counts"]["total"], 5)
        self.assertEqual(metrics["lead_counts"]["open"], 2)
        self.assertEqual(metrics["lead_counts"]["converted"], 2)
        self.assertEqual(metrics["lead_counts"]["due_today"], 1)
        self.assertEqual(metrics["lead_counts"]["overdue"], 1)
        self.assertEqual(metrics["opportunity_counts"]["open"], 1)
        self.assertEqual(metrics["quotation_counts"], {"open": 2, "approved": 1})
        self.assertEqual(metrics["closing_ratio"], Decimal("66.67"))
        sales = {row["currency"]: row["amount"] for row in metrics["sales_revenue"]}
        self.assertEqual(sales, {"CAD": Decimal("0"), "USD": Decimal("1500"), "BDT": Decimal("0")})
        invoices = {row["currency"]: row for row in metrics["invoice_values"]}
        self.assertEqual(invoices["CAD"]["amount"], Decimal("800"))
        self.assertEqual(invoices["USD"]["amount"], Decimal("600"))
        self.assertEqual(invoices["BDT"]["amount"], Decimal("10000"))
        paid = {row["currency"]: row["amount"] for row in metrics["paid_invoice_values"]}
        self.assertEqual(paid, {"CAD": Decimal("0"), "USD": Decimal("0"), "BDT": Decimal("0")})
        self.assertEqual(metrics["paid_invoice_count"], 0)

    def test_closed_won_timestamp_is_set_once(self):
        opportunity = Opportunity.objects.create(
            lead=self.open_lead,
            stage="Proposal",
            order_currency="CAD",
            order_value=Decimal("250"),
        )
        self.assertIsNone(opportunity.closed_won_at)
        opportunity.stage = "Closed Won"
        opportunity.save(update_fields=["stage"])
        first_closed_at = opportunity.closed_won_at
        self.assertIsNotNone(first_closed_at)
        opportunity.notes = "Later update"
        opportunity.save(update_fields=["notes"])
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.closed_won_at, first_closed_at)

    def test_metrics_have_bounded_query_count(self):
        with CaptureQueriesContext(connection) as queries:
            build_salesperson_profile(self.sales)
        self.assertLessEqual(len(queries), 10)

    def test_profile_page_uses_explicit_currency_labels(self):
        self.client.force_login(self.sales)
        response = self.client.get(reverse("salesperson_profile"))
        self.assertContains(response, "USD $1,500.00")
        self.assertContains(response, "CAD $800.00")
        self.assertContains(response, "৳10,000.00")


class TeamPerformanceDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.ceo_group = Group.objects.get_or_create(name="CEO")[0]
        cls.manager_group = Group.objects.get_or_create(name="Manager")[0]
        cls.sales_group = Group.objects.get_or_create(name="Sales")[0]
        cls.ceo = User.objects.create_user("team-ceo", first_name="Hossain")
        cls.ceo.groups.add(cls.ceo_group)
        cls.manager = User.objects.create_user("team-manager", first_name="Refat")
        cls.manager.groups.add(cls.manager_group)
        cls.manager.employee_profile.department = "sales"
        cls.manager.employee_profile.save()
        cls.sales = User.objects.create_user("team-sales", first_name="Talha")
        cls.sales.groups.add(cls.sales_group)
        cls.sales.employee_profile.display_name = "Talha"
        cls.sales.employee_profile.position = "sales_executive"
        cls.sales.employee_profile.department = "sales"
        cls.sales.employee_profile.save()
        cls.regular = User.objects.create_user("team-regular", first_name="Regular")
        on_leave = User.objects.create_user("team-leave", first_name="Nadia")
        on_leave.employee_profile.status = EmployeeProfile.STATUS_ON_LEAVE
        on_leave.employee_profile.department = "sales"
        on_leave.employee_profile.position = "sales_executive"
        on_leave.employee_profile.save()
        suspended = User.objects.create_user("team-suspended", first_name="Omar")
        suspended.employee_profile.status = EmployeeProfile.STATUS_SUSPENDED
        suspended.employee_profile.department = "production"
        suspended.employee_profile.position = "production_coordinator"
        suspended.employee_profile.save()
        lead = Lead.objects.create(
            account_brand="Team Performance Brand",
            assigned_to=cls.sales,
            next_followup=timezone.localdate() - timedelta(days=1),
        )
        Opportunity.objects.create(
            lead=lead,
            stage="Closed Won",
            is_open=False,
            order_currency="CAD",
            order_value=Decimal("363000"),
            order_value_usd=Decimal("3000"),
        )
        Opportunity.objects.create(
            lead=lead,
            stage="Closed Won",
            is_open=False,
            order_currency="USD",
            order_value=Decimal("2000"),
        )
        LeadActivity.objects.create(
            lead=lead,
            user=cls.sales,
            activity_type="follow_up_sent",
            description="Follow up completed",
        )

    def setUp(self):
        self.client = Client()

    def test_ceo_and_manager_can_view_but_regular_user_cannot(self):
        self.client.force_login(self.ceo)
        self.assertEqual(self.client.get(reverse("team_performance")).status_code, 200)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("team_performance")).status_code, 200)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("team_performance")).status_code, 403)

    def test_team_metrics_and_revenue_stay_separated_by_currency(self):
        metrics = build_team_performance()
        self.assertEqual(metrics["top_salesperson"]["name"], "Talha")
        self.assertEqual(metrics["top_salesperson"]["won"], 2)
        revenue = {row["currency"]: row["amount"] for row in metrics["revenue_leaders"]}
        self.assertEqual(
            revenue,
            {"CAD": Decimal("0"), "USD": Decimal("5000"), "BDT": Decimal("0")},
        )
        self.assertEqual(metrics["most_followups_completed"]["completed_followups"], 1)
        self.assertEqual(metrics["most_overdue_followups"]["overdue_followups"], 1)
        self.assertEqual([profile.public_name for profile in metrics["employees_on_leave"]], ["Nadia"])
        self.assertEqual([profile.public_name for profile in metrics["suspended_employees"]], ["Omar"])

    def test_team_page_uses_explicit_currency_labels(self):
        self.client.force_login(self.ceo)
        response = self.client.get(reverse("team_performance"))
        self.assertContains(response, "CAD $0.00")
        self.assertContains(response, "USD $5,000.00")
        self.assertContains(response, "৳0.00")

    def test_team_service_has_bounded_queries(self):
        with CaptureQueriesContext(connection) as queries:
            build_team_performance()
        self.assertLessEqual(len(queries), 6)
