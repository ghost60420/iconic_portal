from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from crm.models import AutomationNotification, ProductionOrder


EXACT_SIGNATURE_FIELDS = (
    "assigned_user_id",
    "assigned_role",
    "notification_type",
    "rule_type",
    "record_content_type_id",
    "record_object_id",
    "target_url",
    "message",
)


def _recipient_key(notification):
    if notification.assigned_user_id:
        return f"user:{notification.assigned_user_id}"
    if notification.assigned_role:
        return f"role:{notification.assigned_role.casefold()}"
    return "broadcast"


def _ordered_ids(queryset):
    return list(queryset.order_by("created_at", "id").values_list("id", flat=True))


class Command(BaseCommand):
    help = (
        "Audit duplicate AutomationNotification rows. Defaults to dry-run; pass "
        "--apply to delete duplicates after reviewing the output."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete duplicate rows. Without this flag the command only reports.",
        )
        parser.add_argument(
            "--include-resolved",
            action="store_true",
            help="Include resolved notification history in the duplicate scan.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum duplicate groups to print. Defaults to 25.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        include_resolved = options["include_resolved"]
        print_limit = max(0, options["limit"])
        groups = self._duplicate_groups(include_resolved=include_resolved)

        duplicate_count = sum(max(0, len(ids) - 1) for _key, ids in groups)
        mode = "APPLY" if apply else "DRY RUN"
        self.stdout.write(f"{mode}: duplicate notification groups found: {len(groups)}")
        self.stdout.write(f"{mode}: duplicate notification rows to delete: {duplicate_count}")
        if not apply:
            self.stdout.write("No notifications deleted. Re-run with --apply after review.")

        for index, (key, ids) in enumerate(groups[:print_limit], start=1):
            self.stdout.write(
                f"{index}. {key} | keep id {ids[0]} | duplicate ids {ids[1:]}"
            )
        if print_limit and len(groups) > print_limit:
            self.stdout.write(f"... {len(groups) - print_limit} more duplicate group(s) hidden by --limit")

        if not apply or not groups:
            return

        deleted_total = 0
        kept_total = 0
        for _key, ids in groups:
            deleted, kept = self._apply_group(ids)
            deleted_total += deleted
            if kept:
                kept_total += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_total} duplicate notification row(s); kept {kept_total} oldest row(s)."
            )
        )

    def _base_queryset(self, *, include_resolved):
        queryset = AutomationNotification.objects.all()
        if not include_resolved:
            queryset = queryset.filter(is_resolved=False)
        return queryset

    def _duplicate_groups(self, *, include_resolved):
        groups = []
        seen = set()
        base = self._base_queryset(include_resolved=include_resolved)
        for signature in (
            base.values(*EXACT_SIGNATURE_FIELDS)
            .annotate(row_count=Count("id"))
            .filter(row_count__gt=1)
            .iterator()
        ):
            filters = {field: signature[field] for field in EXACT_SIGNATURE_FIELDS}
            ids = _ordered_ids(base.filter(**filters))
            if len(ids) > 1:
                key = (
                    "exact",
                    signature["notification_type"],
                    signature["assigned_user_id"] or "",
                    signature["assigned_role"] or "",
                    signature["record_content_type_id"] or "",
                    signature["record_object_id"] or "",
                    signature["target_url"] or "",
                )
                groups.append((key, ids))
                seen.add(frozenset(ids))

        production_groups = self._production_ready_groups(base)
        for key, ids in production_groups:
            frozen = frozenset(ids)
            if len(ids) > 1 and frozen not in seen:
                groups.append((key, ids))
                seen.add(frozen)
        return groups

    def _production_ready_groups(self, base_queryset):
        production_type = ContentType.objects.get_for_model(ProductionOrder, for_concrete_model=False)
        rows = list(
            base_queryset.filter(notification_type="production_created")
            .only(
                "id",
                "assigned_user",
                "assigned_role",
                "record_content_type",
                "record_object_id",
                "target_url",
                "message",
                "created_at",
            )
            .order_by("created_at", "id")
        )
        order_ids = [
            row.record_object_id
            for row in rows
            if row.record_content_type_id == production_type.pk and row.record_object_id
        ]
        opportunity_by_order = dict(
            ProductionOrder.objects.filter(pk__in=order_ids).values_list("id", "opportunity_id")
        )

        grouped = defaultdict(list)
        for row in rows:
            if row.record_content_type_id == production_type.pk and row.record_object_id:
                opportunity_id = opportunity_by_order.get(row.record_object_id)
                if opportunity_id:
                    event_key = f"opportunity:{opportunity_id}"
                else:
                    event_key = f"order:{row.record_object_id}"
            else:
                event_key = f"target:{row.target_url}|message:{row.message}"
            grouped[("production_ready", event_key, _recipient_key(row))].append(row.id)
        return [(key, ids) for key, ids in grouped.items() if len(ids) > 1]

    def _apply_group(self, ids):
        with transaction.atomic():
            rows = list(
                AutomationNotification.objects.select_for_update()
                .filter(id__in=ids)
                .order_by("created_at", "id")
            )
            if len(rows) <= 1:
                return 0, None

            keep = rows[0]
            duplicates = rows[1:]
            duplicate_ids = [row.id for row in duplicates]

            keep_is_read = all(row.is_read for row in rows)
            read_times = [row.read_at for row in rows if row.read_at]
            keep_is_resolved = all(row.is_resolved for row in rows)
            resolved_times = [row.resolved_at for row in rows if row.resolved_at]
            update_fields = []

            if keep.is_read != keep_is_read:
                keep.is_read = keep_is_read
                update_fields.append("is_read")
            next_read_at = (min(read_times) if keep_is_read and read_times else None)
            if keep_is_read and next_read_at is None:
                next_read_at = timezone.now()
            if keep.read_at != next_read_at:
                keep.read_at = next_read_at
                update_fields.append("read_at")

            if keep.is_resolved != keep_is_resolved:
                keep.is_resolved = keep_is_resolved
                update_fields.append("is_resolved")
            next_resolved_at = (min(resolved_times) if keep_is_resolved and resolved_times else None)
            if keep_is_resolved and next_resolved_at is None:
                next_resolved_at = timezone.now()
            if keep.resolved_at != next_resolved_at:
                keep.resolved_at = next_resolved_at
                update_fields.append("resolved_at")

            if update_fields:
                keep.save(update_fields=[*update_fields, "updated_at"])
            AutomationNotification.objects.filter(id__in=duplicate_ids).delete()
            return len(duplicate_ids), keep.id
