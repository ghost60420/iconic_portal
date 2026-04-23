from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("whatsapp", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappthread",
            name="last_inbound_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="whatsappthread",
            name="last_outbound_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="whatsappthread",
            name="unread_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="whatsappmessage",
            name="media_path",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="whatsappsendqueue",
            name="media_path",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="whatsappsendqueue",
            name="media_mime",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="whatsappsendqueue",
            name="media_filename",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
    ]
