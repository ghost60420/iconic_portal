from django.core.management.base import BaseCommand

from crm.services.operations_notifications import sync_operations_notifications


class Command(BaseCommand):
    help = "Refresh CRM-only production, shipping, and overdue invoice notifications."

    def handle(self, *args, **options):
        result = sync_operations_notifications(force=True)
        if result["error"]:
            raise RuntimeError(result["error"])
        self.stdout.write(self.style.SUCCESS(f"Operations notifications active: {result['active']}"))
