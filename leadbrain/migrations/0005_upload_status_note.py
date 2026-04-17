# Generated manually for Lead Brain Lite upload status notes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadbrain", "0004_upload_job_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadbrainupload",
            name="status_note",
            field=models.TextField(blank=True),
        ),
    ]
