from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crm", "0140_costing_invoice_workflow"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderLifecycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("lead", "Lead"),
                            ("costing", "Costing"),
                            ("quotation", "Quotation"),
                            ("invoice", "Invoice"),
                            ("production", "Production"),
                            ("shipping", "Shipping"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="lead",
                        max_length=20,
                    ),
                ),
                ("estimated_revenue", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("estimated_cost", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("estimated_profit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("estimated_margin", models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "costing",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles_as_costing",
                        to="crm.costingheader",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_order_lifecycles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "customer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles",
                        to="crm.customer",
                    ),
                ),
                (
                    "invoice",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles",
                        to="crm.invoice",
                    ),
                ),
                (
                    "lead",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles",
                        to="crm.lead",
                    ),
                ),
                (
                    "opportunity",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles",
                        to="crm.opportunity",
                    ),
                ),
                (
                    "production_order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles",
                        to="crm.productionorder",
                    ),
                ),
                (
                    "quotation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles_as_quotation",
                        to="crm.costingheader",
                    ),
                ),
                (
                    "shipping_record",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_lifecycles",
                        to="crm.shipment",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
                "indexes": [
                    models.Index(fields=["status", "updated_at"], name="crm_orderli_status_c68bde_idx"),
                    models.Index(fields=["customer", "status"], name="crm_orderli_custome_ec8889_idx"),
                    models.Index(fields=["opportunity", "status"], name="crm_orderli_opportu_bc08af_idx"),
                ],
            },
        ),
    ]
