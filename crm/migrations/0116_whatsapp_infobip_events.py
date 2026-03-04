from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0115_productionorderattachment_line"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappmessage",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("sent", "Sent"),
                    ("delivered", "Delivered"),
                    ("read", "Read"),
                    ("failed", "Failed"),
                    ("received", "Received"),
                ],
                default="received",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="WhatsAppWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "provider",
                    models.CharField(
                        choices=[("infobip", "Infobip"), ("meta", "Meta"), ("web", "Web")],
                        default="infobip",
                        max_length=20,
                    ),
                ),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("new", "New"),
                            ("processing", "Processing"),
                            ("processed", "Processed"),
                            ("failed", "Failed"),
                        ],
                        default="new",
                        max_length=20,
                    ),
                ),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
            ],
            options={
                "ordering": ("-received_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="whatsappwebhookevent",
            index=models.Index(fields=["provider", "status"], name="crm_whatsa_provide_5d0f74_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappwebhookevent",
            index=models.Index(fields=["received_at"], name="crm_whatsa_receive_9a5f5d_idx"),
        ),
    ]
