from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0116_whatsapp_infobip_events"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappthread",
            name="last_inbound_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="WhatsAppProviderLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("infobip", "Infobip"), ("meta", "Meta"), ("web", "Web")], max_length=20)),
                ("direction", models.CharField(choices=[("outbound", "Outbound"), ("inbound", "Inbound")], max_length=10)),
                ("endpoint", models.CharField(max_length=200)),
                ("status_code", models.IntegerField(blank=True, null=True)),
                ("ok", models.BooleanField(default=False)),
                ("request_json", models.JSONField(blank=True, default=dict)),
                ("response_json", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="crm.whatsappmessage")),
                ("thread", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="crm.whatsappthread")),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="whatsappproviderlog",
            index=models.Index(fields=["provider", "direction"], name="crm_whatsap_provider_3d8d2a_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappproviderlog",
            index=models.Index(fields=["created_at"], name="crm_whatsap_created_8fef63_idx"),
        ),
    ]
