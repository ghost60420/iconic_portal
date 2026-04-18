import os
import subprocess
import sys
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from leadbrain.models import LeadBrainWorker


def launch_upload_processing(upload_id: int) -> None:
    worker_name = "default"
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=45)
    with transaction.atomic():
        worker, _created = LeadBrainWorker.objects.select_for_update().get_or_create(
            name=worker_name,
            defaults={
                "status": LeadBrainWorker.STATUS_STARTING,
                "heartbeat_at": now,
                "started_at": now,
                "current_upload_id": upload_id,
            },
        )
        if (
            worker.status
            in [
                LeadBrainWorker.STATUS_STARTING,
                LeadBrainWorker.STATUS_IDLE,
                LeadBrainWorker.STATUS_RUNNING,
            ]
            and worker.heartbeat_at
            and worker.heartbeat_at >= stale_cutoff
        ):
            return

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

    command = [
        sys.executable,
        "manage.py",
        "run_leadbrain_worker",
        "--worker",
        worker_name,
        "--batch-size",
        "100",
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
