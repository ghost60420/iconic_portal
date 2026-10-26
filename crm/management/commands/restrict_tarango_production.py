"""Identity-guarded, auditable activation; dry run unless explicitly applied."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crm.models import SystemActivityLog
from crm.models_access import UserAccess
from crm.services.restricted_production import RESTRICTED_PRODUCTION_GROUP


class Command(BaseCommand):
    help = "Preview or apply assigned-production-only access to verified Tarango (ID 16)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--actor", type=int, help="Existing approving CEO/superuser ID.")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.select_for_update().filter(pk=16).first()
        if user is None or (user.username, user.first_name, user.last_name) != ("Tarango", "Tarango", "Hossain"):
            raise CommandError("STOP: user ID 16 is not the verified Tarango Hossain account.")
        if not user.is_active:
            raise CommandError("STOP: Tarango's account is inactive; review before changing access.")
        access = UserAccess.objects.select_for_update().filter(user=user).first()
        if access is None:
            raise CommandError("STOP: verified account has no existing UserAccess record.")
        flags = [field.name for field in UserAccess._meta.fields if field.get_internal_type() == "BooleanField"]

        def snapshot():
            return {
                "user_id": user.pk,
                "username": user.username,
                "is_superuser": user.is_superuser,
                "is_staff": user.is_staff,
                "groups": sorted(user.groups.values_list("name", flat=True)),
                "direct_permissions": sorted(user.user_permissions.values_list("pk", flat=True)),
                "flags": {name: getattr(access, name) for name in flags},
            }

        before = snapshot()
        desired = {
            **before,
            "is_superuser": False,
            "is_staff": False,
            "groups": [RESTRICTED_PRODUCTION_GROUP],
            "direct_permissions": [],
            "flags": {name: name == "can_production" for name in flags},
        }
        if not options["apply"]:
            self.stdout.write(json.dumps({"dry_run": True, "before": before, "proposed": desired}, sort_keys=True))
            return
        actor = User.objects.filter(pk=options.get("actor"), is_active=True).first()
        if actor is None or actor.pk == user.pk or not (actor.is_superuser or actor.groups.filter(name="CEO").exists()):
            raise CommandError("STOP: --actor must identify another active CEO or superuser.")
        group = Group.objects.filter(name=RESTRICTED_PRODUCTION_GROUP).first()
        if group and (group.permissions.exists() or group.user_set.exclude(pk=user.pk).exists()):
            raise CommandError("STOP: restriction marker must have no granted permissions or other account members.")
        if before == desired:
            self.stdout.write("Already restricted; no records changed.")
            return
        group, _created = Group.objects.get_or_create(name=RESTRICTED_PRODUCTION_GROUP)
        user.is_superuser = False
        user.is_staff = False
        user.save(update_fields=["is_superuser", "is_staff"])
        user.groups.set([group])
        user.user_permissions.clear()
        access.user = user
        for name, value in desired["flags"].items():
            setattr(access, name, value)
        access.save(update_fields=[*flags, "updated_at"])
        after = snapshot()
        if after != desired:
            raise CommandError("STOP: resulting permissions do not match the approved restriction.")
        SystemActivityLog.objects.create(
            actor=actor, area="permissions", action="restrict_production_access",
            model_label=User._meta.label, object_id=str(user.pk), method="COMMAND",
            message="Restrict verified Tarango account to assigned production orders only.",
            meta_json=json.dumps({
                "reason": "Owner approved Tarango-only production access; no historical orders assigned.",
                "before": before, "after": after,
            }, sort_keys=True),
        )
        self.stdout.write("Restricted verified Tarango (ID 16). Audit recorded; production assignments unchanged.")
