from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0136_add_sports_product_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="order_currency",
            field=models.CharField(
                choices=[("CAD", "CAD"), ("USD", "USD")],
                default="CAD",
                max_length=3,
            ),
        ),
    ]
