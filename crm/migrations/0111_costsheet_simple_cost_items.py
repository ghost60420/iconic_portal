from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0110_merge_0101_merge_0099_0100_0109_productionorderline"),
    ]

    operations = [
        migrations.AddField(
            model_name="costsheetsimple",
            name="rib_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="woven_fabric_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="zipper_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="zipper_puller_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="button_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="thread_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="lining_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="velcro_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="neck_tape_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="elastic_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="collar_cuff_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="ring_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="buckle_clip_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="main_label_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="care_label_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="hang_tag_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="costsheetsimple",
            name="conveyance_cost_per_piece",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=12),
        ),
    ]
