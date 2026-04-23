# Generated manually for isolated Lead Brain discovery backend changes.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadbrain", "0016_leadbraindiscoveryjob_apparel_only_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="countries_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="max_results_per_run",
            field=models.PositiveIntegerField(default=25),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="min_fit_score",
            field=models.PositiveSmallIntegerField(default=65),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="niches_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="run_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryjob",
            name="source_types_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="leadbraincompany",
            name="discovery_job",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="discovered_companies",
                to="leadbrain.leadbraindiscoveryjob",
            ),
        ),
        migrations.AddField(
            model_name="leadbraincompany",
            name="discovery_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="discovered_companies",
                to="leadbrain.leadbraindiscoveryrun",
            ),
        ),
        migrations.AddField(
            model_name="leadbraincompany",
            name="source_detail",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="leadbraincompany",
            name="source_type",
            field=models.CharField(blank=True, db_index=True, max_length=40),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="error_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="queries_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="total_candidates_found",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="total_candidates_saved",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="total_duplicates_skipped",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="total_failed",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="total_weak_skipped",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="leadbraindiscoveryrun",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="leadbraindiscoveryrun",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="leadbraindiscoveryrun",
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="LeadBrainDiscoveryCandidate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_name", models.CharField(blank=True, max_length=255)),
                ("website", models.URLField(blank=True)),
                ("source_type", models.CharField(blank=True, max_length=40)),
                ("source_url", models.URLField(blank=True)),
                ("country", models.CharField(blank=True, max_length=100)),
                ("niche", models.CharField(blank=True, max_length=100)),
                (
                    "discovery_status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("processing", "Processing"),
                            ("saved", "Saved"),
                            ("duplicate", "Duplicate"),
                            ("weak", "Weak"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=20,
                    ),
                ),
                ("research_json", models.JSONField(blank=True, default=dict)),
                ("fit_score", models.PositiveIntegerField(default=0)),
                ("fit_label", models.CharField(blank=True, default="", max_length=20)),
                ("skip_reason", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_leadbrain_company",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="discovery_candidates",
                        to="leadbrain.leadbraincompany",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="candidates",
                        to="leadbrain.leadbraindiscoveryrun",
                    ),
                ),
            ],
            options={
                "ordering": ["run_id", "id"],
                "indexes": [
                    models.Index(fields=["run", "discovery_status"], name="leadbrain_l_run_id_b6a48f_idx"),
                    models.Index(fields=["website"], name="leadbrain_l_website_d43d6b_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="leadbraindiscoveryrun",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["queued", "processing"])),
                fields=("job",),
                name="leadbrain_one_active_discovery_run_per_job",
            ),
        ),
    ]
