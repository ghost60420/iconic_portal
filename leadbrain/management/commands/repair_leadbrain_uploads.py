import hashlib
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from leadbrain.models import LeadBrainCompany, LeadBrainUpload


STALE_MINUTES_DEFAULT = 60


def _compute_file_hash(upload: LeadBrainUpload) -> str:
    if not upload.file:
        return ""
    digest = hashlib.sha256()
    with upload.file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Inspect or repair stale and duplicate Lead Brain Lite uploads."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply the repair actions.")
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=STALE_MINUTES_DEFAULT,
            help="Mark processing uploads older than this threshold as stale.",
        )
        parser.add_argument(
            "--flag-duplicates",
            action="store_true",
            help="Flag older duplicate upload jobs for review without deleting them.",
        )
        parser.add_argument(
            "--backfill-hashes",
            action="store_true",
            help="Compute missing file hashes for older uploads before duplicate review.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        stale_minutes = max(1, options["stale_minutes"])
        flag_duplicates = options["flag_duplicates"]
        backfill_hashes = options["backfill_hashes"]
        stale_cutoff = timezone.now() - timedelta(minutes=stale_minutes)

        stale_uploads = LeadBrainUpload.objects.filter(
            status=LeadBrainUpload.STATUS_PROCESSING,
            updated_at__lt=stale_cutoff,
        ).order_by("updated_at", "id")
        self.stdout.write(f"STALE_UPLOADS {stale_uploads.count()}")

        for upload in stale_uploads:
            self.stdout.write(
                f"STALE upload={upload.pk} file={upload.file_name or '-'} "
                f"updated_at={upload.updated_at:%Y-%m-%d %H:%M:%S}"
            )
            if not apply_changes:
                continue

            processing_rows = upload.companies.filter(research_status=LeadBrainCompany.STATUS_PROCESSING)
            processing_rows.update(
                research_status=LeadBrainCompany.STATUS_FAILED,
                research_error=f"Marked failed by repair_leadbrain_uploads after {stale_minutes} stale minutes.",
                processed_at=timezone.now(),
            )
            upload.refresh_progress(save=False)
            upload.status = (
                LeadBrainUpload.STATUS_PARTIAL
                if upload.completed_rows
                else LeadBrainUpload.STATUS_FAILED
            )
            upload.status_note = (
                f"Marked failed by repair_leadbrain_uploads after {stale_minutes} stale minutes."
            )
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
                    "status_note",
                    "updated_at",
                ]
            )

        if backfill_hashes:
            missing_hashes = LeadBrainUpload.objects.filter(file_hash="").order_by("-uploaded_at", "-id")
            self.stdout.write(f"MISSING_HASHES {missing_hashes.count()}")
            for upload in missing_hashes:
                try:
                    file_hash = _compute_file_hash(upload)
                except Exception as exc:
                    self.stdout.write(
                        f"HASH_ERROR upload={upload.pk} file={upload.file_name or '-'} error={exc}"
                    )
                    continue
                if not file_hash:
                    continue
                self.stdout.write(f"HASH upload={upload.pk} hash={file_hash[:12]}")
                if apply_changes:
                    active_conflict = (
                        LeadBrainUpload.objects.filter(
                            uploaded_by_id=upload.uploaded_by_id,
                            file_hash=file_hash,
                            status__in=[LeadBrainUpload.STATUS_PENDING, LeadBrainUpload.STATUS_PROCESSING],
                        )
                        .exclude(pk=upload.pk)
                        .order_by("-uploaded_at", "-id")
                        .first()
                    )
                    if active_conflict and upload.status in [
                        LeadBrainUpload.STATUS_PENDING,
                        LeadBrainUpload.STATUS_PROCESSING,
                    ]:
                        upload.status = LeadBrainUpload.STATUS_FAILED
                        upload.status_note = (
                            f"Duplicate upload history for review. Newer active upload job is #{active_conflict.pk}."
                        )
                        upload.save(update_fields=["status", "status_note", "updated_at"])
                    upload.file_hash = file_hash
                    upload.save(update_fields=["file_hash", "updated_at"])

        duplicate_groups = list(
            LeadBrainUpload.objects.exclude(file_hash="")
            .values("uploaded_by_id", "file_hash")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
            .order_by("uploaded_by_id", "file_hash")
        )
        self.stdout.write(f"DUPLICATE_GROUPS {len(duplicate_groups)}")

        for group in duplicate_groups:
            uploads = list(
                LeadBrainUpload.objects.filter(
                    uploaded_by_id=group["uploaded_by_id"],
                    file_hash=group["file_hash"],
                ).order_by("-uploaded_at", "-id")
            )
            newest = uploads[0]
            older_ids = [upload.pk for upload in uploads[1:]]
            self.stdout.write(
                f"DUPLICATE uploaded_by={group['uploaded_by_id']} hash={group['file_hash'][:12]} "
                f"newest={newest.pk} older={older_ids}"
            )
            if not (apply_changes and flag_duplicates):
                continue

            note = f"Duplicate upload history for review. Newer upload job is #{newest.pk}."
            LeadBrainUpload.objects.filter(pk__in=older_ids).update(status_note=note, updated_at=timezone.now())

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to update records."))
