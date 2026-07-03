from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0177_employee_profile_archive_and_ceo_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionorder",
            name="completed_quantity",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="productionorder",
            name="extra_local_cost_bdt",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="productionorder",
            name="sewing_charge_per_piece_bdt",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="productionorder",
            name="sewing_cost_per_piece_bdt",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
