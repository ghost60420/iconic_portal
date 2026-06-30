import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def correct_ceo_employee_identity(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    EmployeeProfile = apps.get_model("crm", "EmployeeProfile")

    user = (
        User.objects.filter(
            first_name__iexact="Hossain",
            last_name__iexact="Forhad",
            groups__name__iexact="CEO",
        )
        .distinct()
        .order_by("id")
        .first()
    )
    if not user:
        return
    profile = EmployeeProfile.objects.filter(user_id=user.pk).first()
    if not profile:
        return

    aliases = []
    seen = set()
    for value in [*(profile.aliases or []), profile.display_name, "Hossein", "Hussain", "Farhad"]:
        alias = " ".join(str(value or "").split())
        key = alias.casefold()
        if alias and key != "hossain" and key not in seen:
            seen.add(key)
            aliases.append(alias)
    profile.display_name = "Hossain"
    profile.aliases = aliases
    profile.save(update_fields=["display_name", "aliases"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0176_quickcosting_approval_submission"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeprofile",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeeprofile",
            name="archived_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="archived_employee_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="employeeprofile",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(correct_ceo_employee_identity, migrations.RunPython.noop),
    ]
