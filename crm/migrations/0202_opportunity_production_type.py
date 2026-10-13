from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crm", "0201_quick_costing_sample_fields")]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="production_type",
            field=models.CharField(
                blank=True,
                choices=[("", "Unset"), ("in_house", "In House"), ("third_party", "Third Party")],
                default="",
                db_default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="third_party_name",
            field=models.CharField(blank=True, default="", db_default="", max_length=200),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="third_party_notes",
            field=models.TextField(blank=True, default="", db_default=""),
        ),
    ]
