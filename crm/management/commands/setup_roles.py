from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from crm.models import AccountingEntry, BDStaff, BDStaffMonth


class Command(BaseCommand):
    help = "Create default roles and permissions for Iconic CRM"

    def handle(self, *args, **options):
        bd_group, _ = Group.objects.get_or_create(name="BD")
        ca_group, _ = Group.objects.get_or_create(name="CA")

        accounting_ct = ContentType.objects.get_for_model(AccountingEntry)
        staff_ct = ContentType.objects.get_for_model(BDStaff)
        staff_month_ct = ContentType.objects.get_for_model(BDStaffMonth)

        def perms_for(ct, actions):
            out = []
            for action in actions:
                codename = f"{action}_{ct.model}"
                p = Permission.objects.filter(content_type=ct, codename=codename).first()
                if p:
                    out.append(p)
            return out

        bd_perms = []
        bd_perms += perms_for(accounting_ct, ["add", "change", "view"])
        bd_perms += perms_for(staff_ct, ["add", "change", "view"])
        bd_perms += perms_for(staff_month_ct, ["add", "change", "view"])

        ca_perms = []
        ca_perms += perms_for(accounting_ct, ["add", "change", "delete", "view"])
        ca_perms += perms_for(staff_ct, ["add", "change", "delete", "view"])
        ca_perms += perms_for(staff_month_ct, ["add", "change", "delete", "view"])

        bd_group.permissions.set(bd_perms)
        ca_group.permissions.set(ca_perms)

        self.stdout.write(self.style.SUCCESS("Done. Groups BD and CA updated."))