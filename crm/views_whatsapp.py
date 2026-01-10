# crm/views_whatsapp.py
import json
import urllib.request
import hmac
import hashlib

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from crm.models import Lead
from crm.models_whatsapp import WhatsAppThread, WhatsAppMessage


def _digits(s: str) -> str:
    return "".join([c for c in (s or "") if c.isdigit()])


def _wa_api_ready() -> bool:
    return bool(getattr(settings, "WA_TOKEN", "") and getattr(settings, "WA_PHONE_NUMBER_ID", ""))


def _wa_send_text(*, to_phone: str, text: str):
    if not _wa_api_ready():
        return False, "WhatsApp API not configured"

    token = getattr(settings, "WA_TOKEN", "")
    phone_id = getattr(settings, "WA_PHONE_NUMBER_ID", "")
    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = getattr(resp, "status", 200)
            if status >= 300:
                return False, f"HTTP {status}"
    except Exception as e:
        return False, str(e)[:300]

    return True, ""


def _should_flag_human(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in ["price", "quote", "cost", "sample", "timeline", "moq", "urgent"])


def _auto_reply_text() -> str:
    return (
        "Thanks for reaching out to Iconic Apparel House.\n\n"
        "We received your message and shared it with our team.\n"
        "Someone will get back to you shortly.\n\n"
        "If you have reference images or details, feel free to send them."
    )


def _can_auto_reply(thread: WhatsAppThread) -> bool:
    now = timezone.now()
    last_auto = getattr(thread, "last_auto_reply_at", None)
    if last_auto is None:
        return True
    return (now - last_auto).total_seconds() > 300


@login_required
def wa_inbox(request):
    threads = WhatsAppThread.objects.order_by("-last_message_at", "-id")[:200]
    return render(request, "crm/whatsapp/inbox.html", {"threads": threads})


@login_required
def wa_thread(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    msgs = thread.messages.order_by("created_at", "id")
    return render(request, "crm/whatsapp/thread.html", {"thread": thread, "messages": msgs})


@require_POST
@login_required
def wa_send(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    text = (request.POST.get("text") or "").strip()
    if not text:
        return JsonResponse({"ok": False, "error": "Empty message"}, status=400)

    ok, err = _wa_send_text(to_phone=thread.wa_phone, text=text)
    if not ok:
        return JsonResponse({"ok": False, "error": err}, status=500)

    WhatsAppMessage.objects.create(
        thread=thread,
        direction="out",
        body=text,
        created_by=request.user,
    )

    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])

    return JsonResponse({"ok": True})


@require_POST
@login_required
def wa_send_ai_draft(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)

    draft = (request.POST.get("draft") or "").strip()
    if not draft:
        return JsonResponse({"ok": False, "error": "Draft is empty"}, status=400)

    if hasattr(thread, "ai_draft"):
        thread.ai_draft = draft

    ok, err = _wa_send_text(to_phone=thread.wa_phone, text=draft)
    if not ok:
        return JsonResponse({"ok": False, "error": err or "Send failed"}, status=500)

    WhatsAppMessage.objects.create(
        thread=thread,
        direction="out",
        body=draft,
        created_by=request.user,
    )

    thread.last_message_at = timezone.now()

    if hasattr(thread, "ai_sent"):
        thread.ai_sent = True

    update_fields = ["last_message_at"]
    if hasattr(thread, "ai_draft"):
        update_fields.append("ai_draft")
    if hasattr(thread, "ai_sent"):
        update_fields.append("ai_sent")

    thread.save(update_fields=update_fields)

    return JsonResponse({"ok": True})


@csrf_exempt
def wa_webhook(request):
    # 1) GET: Meta verify
    if request.method == "GET":
        mode = request.GET.get("hub.mode", "")
        token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")

        if mode == "subscribe" and token == getattr(settings, "WA_VERIFY_TOKEN", ""):
            return HttpResponse(challenge)

        return HttpResponseForbidden("forbidden")

    # 2) POST: incoming messages
    if request.method == "POST":
        sig = request.headers.get("X-Hub-Signature-256", "")
        app_secret = getattr(settings, "WA_APP_SECRET", "")

        # Verify signature only if secret is set AND signature exists
        if app_secret and sig.startswith("sha256="):
            raw = request.body or b""
            expected = "sha256=" + hmac.new(
                app_secret.encode("utf-8"),
                msg=raw,
                digestmod=hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected, sig):
                return HttpResponseForbidden("bad signature")

        # Parse payload safely
        try:
            data = json.loads((request.body or b"{}").decode("utf-8"))
        except Exception:
            return HttpResponse("ok")

        # If this is not a real WhatsApp payload, just accept
        try:
            entry = (data.get("entry") or [])[0]
            changes = (entry.get("changes") or [])[0]
            value = changes.get("value") or {}
        except Exception:
            return HttpResponse("ok")

        messages = value.get("messages") or []
        contacts = value.get("contacts") or []

        contact_name = ""
        if contacts:
            contact_name = (contacts[0].get("profile") or {}).get("name") or ""

        for m in messages:
            wa_from = _digits(m.get("from") or "")
            body = (m.get("text") or {}).get("body") or ""
            msg_id = m.get("id") or ""

            if not wa_from:
                continue

            thread, _ = WhatsAppThread.objects.get_or_create(wa_phone=wa_from)

            if contact_name and not (thread.wa_name or ""):
                thread.wa_name = contact_name

            if not getattr(thread, "lead_id", None):
                lead = Lead.objects.filter(phone__icontains=wa_from).order_by("-id").first()
                if lead:
                    thread.lead = lead

            WhatsAppMessage.objects.get_or_create(
                thread=thread,
                meta_id=msg_id,
                defaults={"direction": "in", "body": body},
            )

            thread.last_message_at = timezone.now()

            if hasattr(thread, "needs_human"):
                thread.needs_human = _should_flag_human(body)

            if getattr(thread, "ai_enabled", True) and not getattr(thread, "needs_human", False):
                if _wa_api_ready() and _can_auto_reply(thread):
                    reply_text = _auto_reply_text()
                    ok2, _ = _wa_send_text(to_phone=thread.wa_phone, text=reply_text)
                    if ok2:
                        WhatsAppMessage.objects.create(
                            thread=thread,
                            direction="out",
                            body=reply_text,
                        )
                        if hasattr(thread, "last_auto_reply_at"):
                            thread.last_auto_reply_at = timezone.now()

            thread.save()

        return HttpResponse("ok")

    return HttpResponseForbidden("forbidden")