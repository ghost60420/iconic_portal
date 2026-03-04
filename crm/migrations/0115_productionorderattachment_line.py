from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0114_invoice_terms_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionorderattachment",
            name="line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attachments",
                to="crm.productionorderline",
            ),
        ),
    ]
