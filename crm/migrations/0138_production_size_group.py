from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0137_add_opportunity_order_currency"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionorder",
            name="size_group",
            field=models.CharField(
                choices=[
                    ("men", "Men"),
                    ("women", "Women"),
                    ("kids", "Kids"),
                    ("youth", "Youth"),
                    ("unisex", "Unisex"),
                ],
                default="unisex",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="productionorderline",
            name="size_group",
            field=models.CharField(
                choices=[
                    ("men", "Men"),
                    ("women", "Women"),
                    ("kids", "Kids"),
                    ("youth", "Youth"),
                    ("unisex", "Unisex"),
                ],
                default="unisex",
                max_length=20,
            ),
        ),
    ]
