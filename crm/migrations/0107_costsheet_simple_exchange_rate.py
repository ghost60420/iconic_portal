from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0106_costsheet_simple_and_doc_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="costsheetsimple",
            name="exchange_rate_bdt_per_cad",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
    ]
