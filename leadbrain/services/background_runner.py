import os
import subprocess
import sys
from math import ceil
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from leadbrain.models import LeadBrainUpload, LeadBrainWorker


WORKER_BASE_NAME = "parallel"
WORKER_BATCH_SIZE = 100
MAX_PARALLEL_WORKERS = 4


def _desired_worker_count(upload_id: int) -> int:
    upload = LeadBrainUpload.objects.filter(pk=upload_id).only("pending_rows", "failed_rows", "total_rows").first()
    if not upload:
        return 1
    queued_rows = max((upload.pending_rows or 0) + (upload.failed_rows or 0), upload.total_rows or 0, 1)
    return max(1, min(MAX_PARALLEL_WORKERS, ceil(queued_rows / WORKER_BATCH_SIZE)))


def launch_upload_processing(upload_id: int) -> None:
    desired_workers = _desired_worker_count(upload_id)
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=45)
    for index in range(1, desired_workers + 1):
        worker_name = f"{WORKER_BASE_NAME}-{index}"
        should_launch = False
        with transaction.atomic():
            worker, created = LeadBrainWorker.objects.select_for_update().get_or_create(
                name=worker_name,
                defaults={
                    "status": LeadBrainWorker.STATUS_STARTING,
                    "heartbeat_at": now,
                    "started_at": now,
                    "current_upload_id": upload_id,
                },
            )
            if created or not (
                worker.status
                in [
                    LeadBrainWorker.STATUS_STARTING,
                    LeadBrainWorker.STATUS_IDLE,
                    LeadBrainWorker.STATUS_RUNNING,
                ]
                and worker.heartbeat_at
                and worker.heartbeat_at >= stale_cutoff
            ):
                worker.status = LeadBrainWorker.STATUS_STARTING
                worker.heartbeat_at = now
                worker.started_at = now
                worker.pid = None
                worker.current_upload_id = upload_id
                worker.last_error = ""
                worker.save(
                    update_fields=[
                        "status",
                        "heartbeat_at",
                        "started_at",
                        "pid",
                        "current_upload",
                        "last_error",
                        "updated_at",
                    ]
                )
                should_launch = True

        if not should_launch:
            continue

        command = [
            sys.executable,
            "manage.py",
            "run_leadbrain_worker",
            "--worker",
            worker_name,
            "--batch-size",
            str(WORKER_BATCH_SIZE),
            "--poll-seconds",
            "5",
            "--idle-shutdown-seconds",
            "600",
        ]
        try:
            subprocess.Popen(
                command,
                cwd=str(settings.BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=os.environ.copy(),
            )
        except Exception:
            LeadBrainWorker.objects.filter(name=worker_name).update(
                status=LeadBrainWorker.STATUS_FAILED,
                last_error="Lead Brain worker process could not be started.",
                updated_at=timezone.now(),
            )
            raise
