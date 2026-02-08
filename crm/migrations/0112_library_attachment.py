from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0111_costsheet_simple_cost_items"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("general", "General"),
                            ("catalog", "Catalog"),
                            ("product", "Product"),
                            ("fabric", "Fabric"),
                            ("accessory", "Accessory"),
                            ("trim", "Trim"),
                            ("thread", "Thread"),
                            ("factory", "Factory"),
                        ],
                        default="general",
                        max_length=20,
                    ),
                ),
                ("file", models.FileField(upload_to="library/")),
                ("original_name", models.CharField(blank=True, default="", max_length=255)),
                ("note", models.TextField(blank=True, default="")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at", "-id"],
            },
        ),
    ]
