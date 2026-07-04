from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0179_salescommission"),
    ]

    operations = [
        migrations.AddField(
            model_name="quickcosting",
            name="pricing_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("full_package", "Full Package"),
                    ("fob", "FOB"),
                    ("cmt_sewing", "CMT / Sewing Only"),
                ],
                db_index=True,
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sewing_charge_per_piece_bdt",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sewing_cost_per_piece_bdt",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="extra_local_cost_bdt",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="productionorder",
            name="source_quick_costing",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_order",
                to="crm.quickcosting",
            ),
        ),
    ]
