# Generated manually for Lead Brain Lite phase 2 background processing.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadbrain", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadbraincompany",
            name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="leadbraincompany",
            name="processing_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="leadbraincompany",
            name="processing_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("complete", "Complete"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
