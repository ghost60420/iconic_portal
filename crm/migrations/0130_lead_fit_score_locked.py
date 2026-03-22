from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0129_opportunity_converted_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="fit_score_locked",
            field=models.BooleanField(default=False),
        ),
    ]

