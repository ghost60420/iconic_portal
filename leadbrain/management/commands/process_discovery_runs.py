import os
import socket
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from leadbrain.models import LeadBrainWorker
from leadbrain.services.discovery_service import DISCOVERY_DEFAULT_BATCH_SIZE, process_discovery_runs


ACTIVE_WORKER_STATUSES = [
    LeadBrainWorker.STATUS_STARTING,
    LeadBrainWorker.STATUS_IDLE,
    LeadBrainWorker.STATUS_RUNNING,
]


class Command(BaseCommand):
    help = "Process queued Lead Brain discovery runs in small safe batches."

    def add_arguments(self, parser):
        parser.add_argument("--worker", default="discovery-default")
        parser.add_argument("--run-id", type=int, default=0)
        parser.add_argument("--limit", type=int, default=1)
        parser.add_argument("--batch-size", type=int, default=DISCOVERY_DEFAULT_BATCH_SIZE)
        parser.add_argument("--continuous", action="store_true")
        parser.add_argument("--poll-seconds", type=int, default=15)
        parser.add_argument("--idle-exit-seconds", type=int, default=300)

    def handle(self, *args, **options):
        worker_name = (options.get("worker") or "discovery-default").strip() or "discovery-default"
        run_id = int(options.get("run_id") or 0) or None
        limit = max(1, int(options.get("limit") or 1))
        batch_size = max(1, int(options.get("batch_size") or DISCOVERY_DEFAULT_BATCH_SIZE))
        poll_seconds = max(1, int(options.get("poll_seconds") or 15))
        worker = self._acquire_worker(worker_name, stale_seconds=max(45, poll_seconds * 3))
        if not worker:
            self.stdout.write(self.style.WARNING(f"Discovery worker '{worker_name}' is already active."))
            return

        idle_exit_seconds = max(1, int(options.get("idle_exit_seconds") or 300))
        last_work_at = timezone.now()
        processed_batches = 0
        processed_rows = 0

        try:
            if not options.get("continuous"):
                processed = process_discovery_runs(limit=limit, batch_size=batch_size, run_id=run_id)
                processed_batches += 1 if processed else 0
                processed_rows += processed
                self._heartbeat(worker, status=LeadBrainWorker.STATUS_IDLE, processed_batches=processed_batches, processed_rows=processed_rows)
                self.stdout.write(self.style.SUCCESS(f"Processed {processed} discovery candidate batch item(s)."))
                return

            while True:
                self._heartbeat(worker, status=LeadBrainWorker.STATUS_IDLE, processed_batches=processed_batches, processed_rows=processed_rows)
                processed = process_discovery_runs(limit=limit, batch_size=batch_size, run_id=run_id)
                if processed:
                    processed_batches += 1
                    processed_rows += processed
                    last_work_at = timezone.now()
                    self._heartbeat(worker, status=LeadBrainWorker.STATUS_RUNNING, processed_batches=processed_batches, processed_rows=processed_rows)
                    self.stdout.write(f"Processed {processed} discovery candidate batch item(s).")
                    continue
                if (timezone.now() - last_work_at).total_seconds() >= idle_exit_seconds:
                    self.stdout.write("No discovery work found. Exiting continuous processor.")
                    break
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Discovery processor interrupted."))
        except Exception as exc:
            self._heartbeat(
                worker,
                status=LeadBrainWorker.STATUS_FAILED,
                processed_batches=processed_batches,
                processed_rows=processed_rows,
                last_error=str(exc)[:2000],
            )
            raise
        finally:
            self._heartbeat(
                worker,
                status=LeadBrainWorker.STATUS_STOPPED,
                processed_batches=processed_batches,
                processed_rows=processed_rows,
                clear_pid=True,
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

    def _heartbeat(
        self,
        worker: LeadBrainWorker,
        *,
        status: str,
        processed_batches: int,
        processed_rows: int,
        last_error: str = "",
        clear_pid: bool = False,
    ) -> None:
        worker.status = status
        worker.hostname = socket.gethostname()
        worker.pid = None if clear_pid else os.getpid()
        worker.heartbeat_at = timezone.now()
        worker.current_upload = None
        worker.last_error = last_error
        worker.processed_batches = processed_batches
        worker.processed_rows = processed_rows
        worker.save(
            update_fields=[
                "status",
                "hostname",
                "pid",
                "heartbeat_at",
                "current_upload",
                "last_error",
                "processed_batches",
                "processed_rows",
                "updated_at",
            ]
        )
