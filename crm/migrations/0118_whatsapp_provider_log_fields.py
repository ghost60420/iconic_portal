from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0117_whatsapp_inbound_and_logs"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="status_name",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="status_group_name",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="status_group_id",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="error_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="error_description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="request_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="whatsappproviderlog",
            name="provider_message_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
