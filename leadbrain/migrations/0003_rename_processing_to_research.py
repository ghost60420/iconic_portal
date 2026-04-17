# Generated manually for Lead Brain Lite research status naming.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leadbrain", "0002_company_processing_fields"),
    ]

    operations = [
        migrations.RenameField(
            model_name="leadbraincompany",
            old_name="processing_error",
            new_name="research_error",
        ),
        migrations.RenameField(
            model_name="leadbraincompany",
            old_name="processing_status",
            new_name="research_status",
        ),
        migrations.AlterField(
            model_name="leadbraincompany",
            name="fit_label",
            field=models.CharField(
                blank=True,
                choices=[
                    ("good_fit", "Good Fit"),
                    ("possible_fit", "Possible Fit"),
                    ("weak_fit", "Weak Fit"),
                ],
                default="",
                max_length=20,
            ),
        ),
    ]
