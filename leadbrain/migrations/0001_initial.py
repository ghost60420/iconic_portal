# Generated manually for Lead Brain Lite initial schema.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LeadBrainUpload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="leadbrain/uploads/")),
                ("file_name", models.CharField(blank=True, max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "status",
                    models.CharField(
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
                ("row_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="leadbrain_uploads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="LeadBrainCompany",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("row_number", models.PositiveIntegerField(default=0)),
                ("company_name", models.CharField(blank=True, max_length=255)),
                ("website", models.URLField(blank=True)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=100)),
                ("country", models.CharField(blank=True, max_length=100)),
                ("city", models.CharField(blank=True, max_length=100)),
                ("linkedin_url", models.URLField(blank=True)),
                ("best_contact_name", models.CharField(blank=True, max_length=255)),
                ("best_contact_title", models.CharField(blank=True, max_length=255)),
                ("business_type", models.CharField(blank=True, max_length=255)),
                (
                    "fit_label",
                    models.CharField(
                        choices=[
                            ("good_fit", "Good Fit"),
                            ("possible_fit", "Possible Fit"),
                            ("weak_fit", "Weak Fit"),
                        ],
                        default="weak_fit",
                        max_length=20,
                    ),
                ),
                ("fit_score", models.PositiveIntegerField(default=0)),
                ("ai_summary", models.TextField(blank=True)),
                ("fit_reason", models.TextField(blank=True)),
                ("suggested_action", models.CharField(blank=True, max_length=255)),
                ("raw_row_json", models.JSONField(blank=True, default=dict)),
                ("research_json", models.JSONField(blank=True, default=dict)),
                ("reviewed", models.BooleanField(default=False)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "upload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="companies",
                        to="leadbrain.leadbrainupload",
                    ),
                ),
            ],
            options={
                "ordering": ["-fit_score", "company_name", "id"],
            },
        ),
    ]
