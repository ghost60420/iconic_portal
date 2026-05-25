from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crm", "0139_invoice_internal_costing"),
    ]

    operations = [
        migrations.AlterField(
            model_name="costingauditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("updated", "Updated"),
                    ("approved", "Approved"),
                    ("unlocked", "Unlocked"),
                    ("quoted", "Converted to quotation"),
                    ("invoice_created", "Converted to invoice"),
                    ("production_created", "Converted to production order"),
                    ("exported", "Exported"),
                    ("uploaded_file", "Uploaded file"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="costingheader",
            name="quotation_number",
            field=models.CharField(blank=True, db_index=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="costingheader",
            name="quoted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="costingheader",
            name="quoted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="quoted_costing_headers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="costing_header",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices",
                to="crm.costingheader",
            ),
        ),
    ]
