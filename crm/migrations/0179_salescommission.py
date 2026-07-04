from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crm", "0178_productionorder_local_sewing_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="SalesCommission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("eligible_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(choices=[("BDT", "BDT"), ("CAD", "CAD"), ("USD", "USD")], max_length=3)),
                ("commission_percent", models.DecimalField(decimal_places=2, max_digits=6)),
                ("commission_amount", models.DecimalField(decimal_places=2, editable=False, max_digits=14)),
                ("approval_status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=12)),
                ("paid_status", models.CharField(choices=[("unpaid", "Unpaid"), ("paid", "Paid")], default="unpaid", max_length=10)),
                ("paid_date", models.DateField(blank=True, null=True)),
                ("payment_reference", models.CharField(blank=True, default="", max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_sales_commissions", to=settings.AUTH_USER_MODEL)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sales_commissions", to="crm.invoice")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["approval_status", "paid_status"], name="crm_salesco_approva_19e0df_idx"),
                    models.Index(fields=["currency"], name="crm_salesco_currenc_94f5fd_idx"),
                ],
            },
        ),
    ]
