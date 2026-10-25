from django.db import migrations, models


def add_missing_active_stages(apps, schema_editor):
    """Add only the approved missing stages, without inferring past progress."""
    Order = apps.get_model("crm", "ProductionOrder")
    Stage = apps.get_model("crm", "ProductionStage")
    Audit = apps.get_model("crm", "CRMAuditLog")
    database = schema_editor.connection.alias
    orders = (
        Order.objects.using(database)
        .filter(is_archived=False, stages__isnull=False)
        .exclude(status__in=["done", "completed", "closed_won", "closed_lost", "cancelled"])
        .exclude(operational_status__in=["ready_to_ship", "shipped", "cancelled"])
        .exclude(order_type="sewing_charge", factory_location="bd")
        .order_by("pk").values_list("pk", flat=True).distinct()
    )
    note = (
        "Planned stage added by approved production stage migration 0204. "
        "Historical progress has not been inferred."
    )
    for order_id in orders.iterator():
        # Serialize against writers which use the existing parent order lock.
        Order.objects.using(database).select_for_update().get(pk=order_id)
        existing_keys = set(
            Stage.objects.using(database).filter(order_id=order_id)
            .values_list("stage_key", flat=True)
        )
        for key, name, color in (
            ("fabric", "Fabric", "#26a69a"),
            ("printing", "Printing", "#ec4899"),
        ):
            if key in existing_keys:
                continue
            stage = Stage(
                order_id=order_id, stage_key=key, display_name=name,
                color_tag=color, status="planned", notes=note,
            )
            # Historical model writes must not invoke current application audit
            # signals as well as the explicit migration audit below.
            Stage.objects.using(database).bulk_create([stage])
            Audit.objects.using(database).create(
                actor_id=None, module="production", record_id=str(order_id),
                record_label=f"Stage #{stage.pk}: {name}", action_type="created",
                field_name=f"stage[{stage.pk}].record", previous_value="",
                new_value=note, target_url=f"/production/{order_id}/",
            )


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0203_invoice_cost_shipping_inputs"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionstage",
            name="actual_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="productionstage",
            name="actual_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="productionstage",
            name="stage_key",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("development", "Development"),
                    ("sampling", "Sampling"),
                    ("fabric", "Fabric"),
                    ("cutting", "Cutting"),
                    ("printing", "Printing"),
                    ("sewing", "Sewing"),
                    ("ironing", "Ironing"),
                    ("qc", "QC"),
                    ("finishing", "Finishing"),
                    ("packing", "Packing"),
                    ("shipping", "Shipping"),
                ],
            ),
        ),
        # Reversing schema must not delete stages that employees may have since
        # updated. Full release recovery uses the verified database backup.
        migrations.RunPython(add_missing_active_stages, migrations.RunPython.noop),
    ]
