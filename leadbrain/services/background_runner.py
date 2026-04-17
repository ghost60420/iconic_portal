import os
import subprocess
import sys

from django.conf import settings


def launch_upload_processing(upload_id: int) -> None:
    command = [
        sys.executable,
        "manage.py",
        "process_leadbrain_uploads",
        "--upload",
        str(upload_id),
        "--limit",
        "1",
        "--batch-size",
        "100",
    ]
    subprocess.Popen(
        command,
        cwd=str(settings.BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )
