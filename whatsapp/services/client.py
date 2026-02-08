import json
import urllib.request
from urllib.error import URLError, HTTPError

from django.conf import settings


def _service_url(path: str) -> str:
    base = getattr(settings, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:3127")
    return base.rstrip("/") + path


def _auth_headers():
    secret = getattr(settings, "WHATSAPP_SERVICE_SECRET", "")
    return {"X-WhatsApp-Secret": secret} if secret else {}


def get_status():
    req = urllib.request.Request(_service_url("/status"), headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_qr():
    req = urllib.request.Request(_service_url("/qr"), headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _service_url("/send"),
        data=data,
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except URLError as e:
        return {"ok": False, "error": str(e)[:200]}


def logout():
    req = urllib.request.Request(_service_url("/logout"), headers=_auth_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def refresh():
    req = urllib.request.Request(_service_url("/refresh"), headers=_auth_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))
