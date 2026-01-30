import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def _verify_signature(request) -> bool:
    app_secret = (getattr(settings, "WA_APP_SECRET", "") or "").strip()
    sig = request.headers.get("X-Hub-Signature-256", "")

    if not app_secret:
        # If no secret configured, skip signature validation
        return True

    if not sig.startswith("sha256="):
        return False

    raw = request.body or b""
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        msg=raw,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)

@csrf_exempt
def whatsapp_webhook(request):
    # Meta verify (GET)
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        verify_token = (getattr(settings, "WA_VERIFY_TOKEN", "") or "").strip()
        if mode == "subscribe" and token and token == verify_token and challenge:
            return HttpResponse(challenge, content_type="text/plain", status=200)

        return HttpResponse("Invalid token", status=403)

    # Incoming events (POST)
    if request.method == "POST":
        if not _verify_signature(request):
            return HttpResponse("Invalid signature", status=403)

        try:
            body = request.body.decode("utf-8") if request.body else ""
            data = json.loads(body) if body else {}
        except Exception:
            return JsonResponse({"status": "bad_json"}, status=400)

        logger.info("WhatsApp webhook received", extra={"has_payload": bool(data)})

        # Always reply 200 fast, Meta needs this
        return JsonResponse({"status": "ok"}, status=200)

    return HttpResponse("Method not allowed", status=405)
