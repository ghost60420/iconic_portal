from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0122_opportunitydocument_costing_header"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionorder",
            name="costing_header",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="production_orders",
                to="crm.costingheader",
            ),
        ),
    ]
