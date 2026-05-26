from django.conf import settings
from django.db import migrations, models


def grant_superuser_internal_costing(apps, schema_editor):
    UserAccess = apps.get_model("crm", "UserAccess")
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app_label, user_model_name)
    superuser_ids = list(User.objects.filter(is_superuser=True).values_list("id", flat=True))
    if superuser_ids:
        UserAccess.objects.filter(user_id__in=superuser_ids).update(can_view_internal_costing=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('crm', '0142_whatsappmessage_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='useraccess',
            name='can_view_internal_costing',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(grant_superuser_internal_costing, migrations.RunPython.noop),
    ]
