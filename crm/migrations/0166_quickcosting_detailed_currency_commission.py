from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0165_productionorderline_quantity"),
    ]

    operations = [
        migrations.AddField(
            model_name="quickcosting",
            name="currency",
            field=models.CharField(
                blank=True,
                choices=[("BDT", "BDT"), ("CAD", "CAD"), ("USD", "USD")],
                default=None,
                max_length=3,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="quickcosting",
            name="currency",
            field=models.CharField(
                blank=True,
                choices=[("BDT", "BDT"), ("CAD", "CAD"), ("USD", "USD")],
                default="BDT",
                max_length=3,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="commission_percent",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="fabric_cost_per_kg",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="fabric_consumption_kg_per_piece",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="making_cost_per_piece",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="packaging_cost_per_piece",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="print_embroidery_cost_per_piece",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="trims_cost_per_piece",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
