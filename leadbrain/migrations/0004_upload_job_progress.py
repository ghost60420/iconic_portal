# Generated manually for Lead Brain Lite upload job progress and duplicate protection.

from django.db import migrations, models
from django.db.models import Q


def backfill_upload_progress(apps, schema_editor):
    Upload = apps.get_model("leadbrain", "LeadBrainUpload")
    Company = apps.get_model("leadbrain", "LeadBrainCompany")

    for upload in Upload.objects.all().iterator():
        companies = Company.objects.filter(upload_id=upload.pk)
        total_rows = companies.count() or upload.row_count or 0
        pending_rows = companies.filter(research_status="pending").count()
        processing_rows = companies.filter(research_status="processing").count()
        completed_rows = companies.filter(research_status="complete").count()
        failed_rows = companies.filter(research_status="failed").count()
        processed_rows = completed_rows + failed_rows

        if total_rows:
            progress_percent = min(100, int((processed_rows * 100) / total_rows))
        else:
            progress_percent = 0

        if not total_rows:
            status = "failed" if upload.status == "failed" else "pending"
        elif completed_rows == total_rows and not failed_rows:
            status = "complete"
        elif failed_rows == total_rows and not completed_rows:
            status = "failed"
        elif processed_rows == total_rows and completed_rows and failed_rows:
            status = "partial"
        elif processing_rows or completed_rows or failed_rows:
            status = "processing"
        else:
            status = "pending"

        upload.row_count = total_rows
        upload.total_rows = total_rows
        upload.pending_rows = pending_rows
        upload.processing_rows = processing_rows
        upload.completed_rows = completed_rows
        upload.failed_rows = failed_rows
        upload.progress_percent = progress_percent
        upload.status = status
        upload.save(
            update_fields=[
                "row_count",
                "total_rows",
                "pending_rows",
                "processing_rows",
                "completed_rows",
                "failed_rows",
                "progress_percent",
                "status",
                "updated_at",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leadbrain", "0003_rename_processing_to_research"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadbrainupload",
            name="completed_rows",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbrainupload",
            name="failed_rows",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbrainupload",
            name="file_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="leadbrainupload",
            name="pending_rows",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbrainupload",
            name="processing_rows",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbrainupload",
            name="progress_percent",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbrainupload",
            name="total_rows",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="leadbrainupload",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("complete", "Complete"),
                    ("failed", "Failed"),
                    ("partial", "Partial"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_upload_progress, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="leadbrainupload",
            constraint=models.UniqueConstraint(
                condition=Q(status__in=["pending", "processing"]) & ~Q(file_hash=""),
                fields=("uploaded_by", "file_hash"),
                name="leadbrain_active_upload_per_user_hash",
            ),
        ),
    ]
