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
        "employeeprofile": ("view", "add", "change"),
    },
    "Director": {
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
        "employeeprofile": ("view",),
    },
    "Manager": {
        "lead": ("view", "change"),
        "opportunity": ("view", "change"),
        "customer": ("view",),
        "costingheader": ("view",),
        "productionorder": ("view", "change"),
        "invoice": ("view",),
    },
    "Sales Manager": {
        "lead": ("view", "add", "change"),
        "opportunity": ("view", "add", "change"),
        "customer": ("view", "add", "change"),
        "costingheader": ("view", "add", "change"),
        "quickcosting": ("view", "add", "change"),
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
    "Merchandiser": {
        "customer": ("view",),
        "costingheader": ("view", "add", "change"),
        "quickcosting": ("view", "add", "change"),
        "productionorder": ("view", "change"),
        "productionstage": ("view", "change"),
    },
    "Supervisor": {},
    "Finance": {
        "customer": ("view",),
        "invoice": ("view",),
        "invoicepayment": ("view",),
        "accountingentry": ("view",),
    },
    "QC": {
        "productionorder": ("view", "change"),
        "productionstage": ("view", "change"),
    },
    "Warehouse": {
        "productionorder": ("view", "change"),
        "shipment": ("view", "change"),
        "inventoryitem": ("view", "change"),
    },
    "HR": {"employeeprofile": ("view", "add", "change")},
    "Admin": {"employeeprofile": ("view", "add", "change")},
    "Read Only": {
        "lead": ("view",),
        "opportunity": ("view",),
        "customer": ("view",),
        "costingheader": ("view",),
        "quickcosting": ("view",),
        "productionorder": ("view",),
        "shipment": ("view",),
        "invoice": ("view",),
        "accountingentry": ("view",),
    },
}

ROLE_EXTRA_PERMISSIONS = {
    "CEO": ("manage_employee_profiles", "view_all_sales_profiles"),
    "Director": ("view_all_sales_profiles",),
    "Manager": ("view_all_sales_profiles",),
    "Sales Manager": ("view_all_sales_profiles",),
    "Admin": ("manage_employee_profiles", "view_all_sales_profiles"),
    "HR": ("manage_employee_profiles",),
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
            permissions.extend(
                Permission.objects.filter(
                    content_type__app_label="crm",
                    codename__in=ROLE_EXTRA_PERMISSIONS.get(role_name, ()),
                )
            )
            group.permissions.add(*permissions)
            verb = "created" if created else "updated"
            self.stdout.write(f"{role_name}: {verb}, {len(permissions)} permission(s) ensured")
        self.stdout.write(self.style.SUCCESS("Operations roles are ready. Existing user assignments were not changed."))
