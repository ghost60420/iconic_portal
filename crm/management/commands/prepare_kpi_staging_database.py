import json
import os
import secrets
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction


TEST_USERS = {
    "kpi_dev_ceo": ("Staging", "CEO"),
    "kpi_dev_manager": ("Staging", "Manager"),
    "kpi_dev_employee": ("Staging", "Employee"),
    "kpi_dev_multi": ("Staging", "Multi Role"),
    "kpi_dev_director": ("Staging", "Director"),
    "kpi_dev_hr": ("Staging", "HR"),
    "kpi_dev_accounts": ("Staging", "Accounts"),
}
SENSITIVE_SETTING_MARKERS = (
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "webhook",
)


class Command(BaseCommand):
    help = (
        "Sanitize the isolated KPI staging database and rotate UAT passwords. "
        "This command refuses to run outside the staging settings profile."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-staging",
            action="store_true",
            help="Required acknowledgement that the selected database is disposable staging.",
        )
        parser.add_argument(
            "--credentials-output",
            required=True,
            help="Owner-only JSON file for the generated UAT passwords.",
        )

    def handle(self, *args, **options):
        self._assert_safe_target(options)
        credentials_path = Path(options["credentials_output"]).expanduser().resolve()
        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        if credentials_path.exists():
            raise CommandError("The credential output already exists; rotate explicitly.")

        with transaction.atomic():
            credentials = self._sanitize_users()
            self._sanitize_employee_profiles()
            self._sanitize_customers()
            self._sanitize_leads()
            self._sanitize_private_content()
            self._clear_credentials()
            self._clear_file_references()
            self._label_staging_policies()

        credentials_path.write_text(
            json.dumps(credentials, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(credentials_path, 0o600)
        self.stdout.write(
            self.style.SUCCESS(
                "Staging database sanitized; IDs retained and seven UAT "
                f"passwords written to {credentials_path} with mode 0600."
            )
        )

    @staticmethod
    def _assert_safe_target(options):
        if not options["confirm_staging"]:
            raise CommandError("--confirm-staging is required.")
        if not getattr(settings, "KPI_STAGING", False):
            raise CommandError("KPI staging settings are required.")
        selected = Path(settings.DATABASES["default"]["NAME"]).resolve()
        production_default = (settings.BASE_DIR / "db.sqlite3").resolve()
        if selected == production_default:
            raise CommandError("Refusing to sanitize the default CRM database.")
        if "staging" not in str(selected).lower():
            raise CommandError("The database path must contain 'staging'.")

    @staticmethod
    def _sanitize_users():
        User = get_user_model()
        missing = sorted(set(TEST_USERS) - set(
            User.objects.filter(username__in=TEST_USERS).values_list(
                "username", flat=True
            )
        ))
        if missing:
            raise CommandError(f"Missing required UAT users: {', '.join(missing)}")

        credentials = {}
        for user in User.objects.order_by("pk"):
            if user.username in TEST_USERS:
                first_name, last_name = TEST_USERS[user.username]
                password = secrets.token_urlsafe(24)
                user.first_name = first_name
                user.last_name = last_name
                user.email = f"{user.username}@example.invalid"
                user.is_active = True
                user.set_password(password)
                credentials[user.username] = password
            else:
                user.username = f"staging_archived_{user.pk}"
                user.first_name = "Staging"
                user.last_name = f"Archived {user.pk}"
                user.email = ""
                user.is_active = False
                user.is_staff = False
                user.is_superuser = False
                user.set_unusable_password()
            user.save(
                update_fields=(
                    "username",
                    "first_name",
                    "last_name",
                    "email",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "password",
                )
            )
        return credentials

    @staticmethod
    def _sanitize_employee_profiles():
        EmployeeProfile = apps.get_model("crm", "EmployeeProfile")
        for profile in EmployeeProfile.objects.select_related("user").order_by("pk"):
            if profile.user.username in TEST_USERS:
                label = " ".join(TEST_USERS[profile.user.username])
            else:
                label = f"Staging Archived Employee {profile.pk}"
            EmployeeProfile.objects.filter(pk=profile.pk).update(
                display_name=label,
                aliases=[],
                phone="",
                profile_photo="",
                notes="",
            )

    @staticmethod
    def _sanitize_customers():
        Customer = apps.get_model("crm", "Customer")
        for customer_id in Customer.objects.values_list("pk", flat=True):
            Customer.objects.filter(pk=customer_id).update(
                account_brand=f"Staging Customer {customer_id}",
                contact_name=f"Staging Contact {customer_id}",
                email=f"customer-{customer_id}@example.invalid",
                phone=f"+155500{customer_id:04d}",
                website="https://example.invalid",
                address_line1="Staging address",
                address_line2="",
                city="Test City",
                state="",
                province="Test Province",
                postal_code="T0T0T0",
                country="Test Country",
                shipping_name=f"Staging Customer {customer_id}",
                shipping_address1="Staging address",
                shipping_address2="",
                shipping_city="Test City",
                shipping_state="Test Province",
                shipping_postcode="T0T0T0",
                shipping_country="Test Country",
                notes="",
            )

    @staticmethod
    def _sanitize_leads():
        Lead = apps.get_model("crm", "Lead")
        available = {field.name for field in Lead._meta.concrete_fields}
        for lead_id in Lead.objects.values_list("pk", flat=True):
            candidates = {
                "company": f"Staging Lead Company {lead_id}",
                "contact_name": f"Staging Lead {lead_id}",
                "email": f"lead-{lead_id}@example.invalid",
                "phone": f"+155510{lead_id:04d}",
                "website": "https://example.invalid",
                "address": "Staging address",
                "notes": "",
                "utm_content": "",
            }
            Lead.objects.filter(pk=lead_id).update(
                **{key: value for key, value in candidates.items() if key in available}
            )

    @staticmethod
    def _sanitize_private_content():
        updates = {
            ("crm", "CustomerNote"): {"author": "Staging", "content": ""},
            ("crm", "LeadActivity"): {"message_copy": ""},
            ("crm", "LeadComment"): {"content": ""},
            ("crm", "OutboundEmailLog"): {
                "to_email": "",
                "subject": "",
                "body": "",
                "error": "",
            },
            ("crm", "EmailThread"): {
                "mailbox": "",
                "subject": "",
                "from_email": "",
                "from_name": "",
            },
            ("crm", "EmailMessage"): {
                "subject": "",
                "from_email": "",
                "from_name": "",
                "to_email": "",
                "body_text": "",
                "body_html": "",
            },
            ("crm", "InvoicePayment"): {"notes": ""},
            ("leadbrain", "LeadBrainCompany"): {
                "company_name": "Staging Company",
                "email": "",
                "phone": "",
                "best_contact_name": "",
                "notes": "",
            },
            ("whatsapp", "WhatsAppMessage"): {
                "body": "",
                "media_url": "",
                "error_text": "",
            },
            ("whatsapp", "WhatsAppSendQueue"): {
                "message_body": "STAGING DISABLED",
                "last_error": "",
                "status": "canceled",
            },
            ("whatsapp", "WhatsAppAutomationRule"): {
                "is_active": False,
                "response_template": "",
            },
        }
        for (app_label, model_name), values in updates.items():
            model = apps.get_model(app_label, model_name)
            available = {field.name for field in model._meta.concrete_fields}
            safe_values = {
                key: value for key, value in values.items() if key in available
            }
            if safe_values:
                model.objects.all().update(**safe_values)

    @staticmethod
    def _clear_credentials():
        credential_models = (
            ("marketing", "OAuthCredential", {
                "encrypted_access_token": "",
                "encrypted_refresh_token": "",
                "account_name": "",
                "account_id": "",
                "scopes": "",
                "is_active": False,
                "last_error": "",
            }),
            ("crm", "EmailInboxConfig", {
                "username": "",
                "password": "",
                "enabled": False,
            }),
        )
        for app_label, model_name, values in credential_models:
            model = apps.get_model(app_label, model_name)
            available = {field.name for field in model._meta.concrete_fields}
            model.objects.all().update(
                **{key: value for key, value in values.items() if key in available}
            )

        CRMSetting = apps.get_model("crm", "CRMSetting")
        for row in CRMSetting.objects.all():
            key = str(getattr(row, "key", "")).lower()
            if any(marker in key for marker in SENSITIVE_SETTING_MARKERS):
                values = {}
                for field_name in ("value", "value_json"):
                    field = next(
                        (
                            field
                            for field in row._meta.concrete_fields
                            if field.name == field_name
                        ),
                        None,
                    )
                    if field:
                        values[field_name] = (
                            {} if isinstance(field, models.JSONField) else ""
                        )
                if values:
                    CRMSetting.objects.filter(pk=row.pk).update(**values)

        InvoiceSettings = apps.get_model("crm", "InvoiceSettings")
        financial_fields = (
            "company_email",
            "company_phone",
            "authorized_by_name",
            "paypal_email_or_id",
            "paypal_qr_image",
            "etransfer_email",
            "canada_bank_name",
            "canada_account_name",
            "canada_account_number",
            "bd_bank_name",
            "bd_account_name",
            "bd_account_number",
            "bd_routing_number",
            "bd_swift",
            "bkash_qr_image",
            "nagad_qr_image",
            "rocket_qr_image",
        )
        available = {field.name for field in InvoiceSettings._meta.concrete_fields}
        InvoiceSettings.objects.all().update(
            **{name: "" for name in financial_fields if name in available}
        )

    @staticmethod
    def _clear_file_references():
        for model in apps.get_models():
            file_fields = [
                field.name
                for field in model._meta.concrete_fields
                if isinstance(field, models.FileField)
            ]
            if file_fields:
                model._base_manager.all().update(
                    **{field_name: "" for field_name in file_fields}
                )

    @staticmethod
    def _label_staging_policies():
        tables = (
            "crm_kpiroletemplate",
            "crm_kpibonusruleset",
            "crm_kpibonusweightprofile",
            "crm_kpiintelligenceruleset",
            "crm_kpinotificationrule",
        )
        with connection.cursor() as cursor:
            existing = {
                table
                for table in tables
                if table in connection.introspection.table_names(cursor)
            }
            for table in sorted(existing):
                cursor.execute(
                    f'UPDATE "{table}" SET "name" = %s || "name" '
                    'WHERE "name" NOT LIKE %s',
                    ["STAGING TEST POLICY - ", "STAGING TEST POLICY - %"],
                )
