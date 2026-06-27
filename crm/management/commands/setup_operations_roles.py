from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


ROLE_MODELS = {
    "CEO": {
        "lead": ("view", "add", "change"),
        "opportunity": ("view", "add", "change"),
        "customer": ("view", "add", "change"),
        "costingheader": ("view", "change"),
        "quickcosting": ("view", "change"),
        "productionorder": ("view", "change"),
        "shipment": ("view", "change"),
        "invoice": ("view", "change"),
        "invoicepayment": ("view", "add", "change"),
        "accountingentry": ("view", "add", "change"),
        "crmauditlog": ("view",),
    },
    "Sales": {
        "lead": ("view", "add", "change"),
        "opportunity": ("view", "add", "change"),
        "customer": ("view", "add", "change"),
        "costingheader": ("view", "add", "change"),
        "quickcosting": ("view", "add", "change"),
    },
    "Production": {
        "productionorder": ("view", "change"),
        "productionstage": ("view", "change"),
        "shipment": ("view", "add", "change"),
    },
    "Accounts": {
        "customer": ("view",),
        "invoice": ("view", "add", "change"),
        "invoicepayment": ("view", "add", "change"),
        "accountingentry": ("view", "add", "change"),
    },
    "Merchandising": {
        "customer": ("view",),
        "costingheader": ("view", "add", "change"),
        "quickcosting": ("view", "add", "change"),
        "productionorder": ("view", "change"),
        "productionstage": ("view", "change"),
    },
}


class Command(BaseCommand):
    help = "Create additive operations groups and model permissions without changing user assignments."

    def handle(self, *args, **options):
        for role_name, model_rules in ROLE_MODELS.items():
            group, created = Group.objects.get_or_create(name=role_name)
            permissions = []
            for model_name, actions in model_rules.items():
                codenames = [f"{action}_{model_name}" for action in actions]
                permissions.extend(
                    Permission.objects.filter(
                        content_type__app_label="crm",
                        content_type__model=model_name,
                        codename__in=codenames,
                    )
                )
            group.permissions.add(*permissions)
            verb = "created" if created else "updated"
            self.stdout.write(f"{role_name}: {verb}, {len(permissions)} permission(s) ensured")
        self.stdout.write(self.style.SUCCESS("Operations roles are ready. Existing user assignments were not changed."))
