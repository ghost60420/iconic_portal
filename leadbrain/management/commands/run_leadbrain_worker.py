import os
import socket
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from leadbrain.models import LeadBrainWorker
from leadbrain.services.processing_service import process_upload_batch, select_next_upload, update_worker_heartbeat


ACTIVE_WORKER_STATUSES = [
    LeadBrainWorker.STATUS_STARTING,
    LeadBrainWorker.STATUS_IDLE,
    LeadBrainWorker.STATUS_RUNNING,
]


class Command(BaseCommand):
    help = "Run a persistent Lead Brain Lite batch worker."

    def add_arguments(self, parser):
        parser.add_argument("--worker", default="default")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--poll-seconds", type=int, default=5)
        parser.add_argument("--idle-shutdown-seconds", type=int, default=600)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        worker_name = (options.get("worker") or "default").strip() or "default"
        batch_size = max(1, options.get("batch_size") or 100)
        poll_seconds = max(1, options.get("poll_seconds") or 5)
        idle_shutdown_seconds = max(1, options.get("idle_shutdown_seconds") or 600)
        worker = self._acquire_worker(worker_name, stale_seconds=max(45, poll_seconds * 3))
        if not worker:
            self.stdout.write(self.style.WARNING(f"Lead Brain worker '{worker_name}' is already active."))
            return

        last_work_at = timezone.now()
        try:
            while True:
                update_worker_heartbeat(worker, status=LeadBrainWorker.STATUS_IDLE, last_error="")
                upload = select_next_upload()
                if upload:
                    processed_rows = process_upload_batch(upload, batch_size=batch_size, worker=worker)
                    if processed_rows:
                        last_work_at = timezone.now()
                        continue

                update_worker_heartbeat(worker, status=LeadBrainWorker.STATUS_IDLE, current_upload=None)
                if options.get("once"):
                    break
                if (timezone.now() - last_work_at).total_seconds() >= idle_shutdown_seconds:
                    break
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Lead Brain worker interrupted."))
        except Exception as exc:
            update_worker_heartbeat(
                worker,
                status=LeadBrainWorker.STATUS_FAILED,
                current_upload=None,
                last_error=str(exc)[:2000],
            )
            raise
        finally:
            update_worker_heartbeat(
                worker,
                status=LeadBrainWorker.STATUS_STOPPED,
                current_upload=None,
                pid=None,
            )

    def _acquire_worker(self, worker_name: str, *, stale_seconds: int) -> LeadBrainWorker | None:
        now = timezone.now()
        stale_cutoff = now - timedelta(seconds=stale_seconds)
        defaults = {
            "status": LeadBrainWorker.STATUS_STARTING,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": now,
            "heartbeat_at": now,
        }
        with transaction.atomic():
            worker, _created = LeadBrainWorker.objects.select_for_update().get_or_create(
                name=worker_name,
                defaults=defaults,
            )
            is_fresh = bool(worker.heartbeat_at and worker.heartbeat_at >= stale_cutoff)
            if worker.status in ACTIVE_WORKER_STATUSES and is_fresh and worker.pid and worker.pid != os.getpid():
                return None

            worker.status = LeadBrainWorker.STATUS_RUNNING
            worker.hostname = socket.gethostname()
            worker.pid = os.getpid()
            worker.started_at = now
            worker.heartbeat_at = now
            worker.current_upload = None
            worker.last_error = ""
            worker.save(
                update_fields=[
                    "status",
                    "hostname",
                    "pid",
                    "started_at",
                    "heartbeat_at",
                    "current_upload",
                    "last_error",
                    "updated_at",
                ]
            )
            return worker
