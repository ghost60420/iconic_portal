import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


CRM_BASELINE = ("crm", "0141_order_lifecycle")
LEGACY_TABLES = ("crm_whatsappthread", "crm_whatsappmessage")
REPLACEMENT_TABLES = ("whatsapp_whatsappthread", "whatsapp_whatsappmessage")


def database_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as database_file:
        for chunk in iter(lambda: database_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = (
        "Restore missing legacy CRM WhatsApp tables at the CRM 0141 boundary. "
        "This guarded command is only for the pre-KPI development-database repair."
    )

    def add_arguments(self, parser):
        parser.add_argument("--expected-sha256", required=True)

    def handle(self, *args, **options):
        database_path = Path(connection.settings_dict["NAME"]).resolve()
        if not database_path.is_file():
            raise CommandError(f"Database does not exist: {database_path}")

        actual_sha256 = database_sha256(database_path)
        if actual_sha256 != options["expected_sha256"]:
            raise CommandError(
                "Database checksum does not match --expected-sha256; no changes made."
            )

        executor = MigrationExecutor(connection)
        applied = executor.loader.applied_migrations
        allowed_crm_migrations = {
            node
            for node in executor.loader.graph.forwards_plan(CRM_BASELINE)
            if node[0] == "crm"
        }
        applied_crm_migrations = {node for node in applied if node[0] == "crm"}
        unexpected_migrations = applied_crm_migrations - allowed_crm_migrations
        if CRM_BASELINE not in applied or unexpected_migrations:
            raise CommandError(
                "CRM ledger must be exactly at or before 0141 with 0141 applied; "
                f"unexpected entries: {sorted(unexpected_migrations)}"
            )

        existing_tables = set(connection.introspection.table_names())
        present_legacy_tables = set(LEGACY_TABLES) & existing_tables
        if present_legacy_tables == set(LEGACY_TABLES):
            self.stdout.write("Legacy CRM WhatsApp tables already exist; no changes made.")
            return
        if present_legacy_tables:
            raise CommandError(
                "Legacy CRM WhatsApp schema is partly present; manual review is required."
            )

        with connection.cursor() as cursor:
            replacement_counts = {}
            for table in REPLACEMENT_TABLES:
                if table not in existing_tables:
                    replacement_counts[table] = None
                    continue
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                replacement_counts[table] = cursor.fetchone()[0]
        if any(replacement_counts.values()):
            raise CommandError(
                "Replacement WhatsApp tables contain data; an explicit data mapping is "
                "required before restoring the legacy schema."
            )

        project_state = executor.loader.project_state([CRM_BASELINE])
        models_to_create = (
            project_state.apps.get_model("crm", "WhatsAppThread"),
            project_state.apps.get_model("crm", "WhatsAppMessage"),
        )

        with connection.constraint_checks_disabled():
            with connection.schema_editor() as schema_editor:
                for model in models_to_create:
                    schema_editor.create_model(model)
            connection.check_constraints()

        restored_tables = set(connection.introspection.table_names())
        missing_after_repair = set(LEGACY_TABLES) - restored_tables
        if missing_after_repair:
            raise CommandError(
                f"Repair did not create required tables: {sorted(missing_after_repair)}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Restored crm_whatsappthread and crm_whatsappmessage without changing "
                "the migration ledger."
            )
        )
