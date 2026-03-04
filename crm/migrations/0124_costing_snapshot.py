from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0123_productionorder_costing_header"),
    ]

    operations = [
        migrations.CreateModel(
            name="CostingSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("source", models.CharField(default="approval", max_length=50)),
                ("data", models.JSONField()),
                ("costing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="snapshots", to="crm.costingheader")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
