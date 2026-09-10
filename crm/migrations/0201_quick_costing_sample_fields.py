from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0200_invoice_payment_methods"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quickcosting",
            name="pricing_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("full_package", "Full Package"),
                    ("fob", "FOB"),
                    ("cmt_sewing", "CMT / Sewing Only"),
                    ("sample", "Sample"),
                ],
                db_index=True,
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_charge",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_development_cost",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_fabric_cost",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_packaging_cost",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_print_embroidery_cost",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_trim_cost",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="quickcosting",
            name="sample_wash_cost",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        ),
    ]
