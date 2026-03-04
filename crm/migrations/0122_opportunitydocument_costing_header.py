from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0121_costing_header_engine"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunitydocument",
            name="costing_header",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="crm.costingheader",
            ),
        ),
    ]
