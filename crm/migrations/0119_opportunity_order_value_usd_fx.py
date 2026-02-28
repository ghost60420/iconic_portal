from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0118_whatsapp_provider_log_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="order_value_usd",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="fx_rate_bdt_per_usd",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
    ]
