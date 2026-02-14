# crm/views_whatsapp.py
import json
import urllib.request
import hmac
import hashlib
import mimetypes
import os
import threading
from uuid import uuid4
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden, FileResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from crm.models import Lead, Event
from crm.models_whatsapp import WhatsAppThread, WhatsAppMessage, WhatsAppWebhookEvent


def _digits(s: str) -> str:
    return "".join([c for c in (s or "") if c.isdigit()])


def _wa_provider() -> str:
    return (getattr(settings, "WHATSAPP_PROVIDER", "meta") or "meta").strip().lower()


def _infobip_base_url() -> str:
    base = (getattr(settings, "WHATSAPP_BASE_URL", "") or "").strip()
    if base and not base.startswith("http"):
        base = f"https://{base}"
    return base.rstrip("/")


def _infobip_api_key() -> str:
    return (getattr(settings, "WHATSAPP_API_KEY", "") or "").strip()


def _infobip_sender() -> str:
    return _digits(getattr(settings, "WHATSAPP_SENDER_NUMBER", "") or "")


def _absolute_media_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    if not base:
        return url
    if not url.startswith("/"):
        url = f"/{url}"
    return f"{base}{url}"


def _wa_api_ready() -> bool:
    provider = _wa_provider()
    if provider == "infobip":
        return bool(_infobip_base_url() and _infobip_api_key() and _infobip_sender())
    return bool(getattr(settings, "WA_TOKEN", "") and getattr(settings, "WA_PHONE_NUMBER_ID", ""))


def _wa_web_gateway_url() -> str:
    return (getattr(settings, "WA_WEB_GATEWAY_URL", "") or "").strip()


def _wa_web_api_key() -> str:
    return (getattr(settings, "WA_WEB_API_KEY", "") or "").strip()


def _wa_web_ingest_token() -> str:
    return (getattr(settings, "WA_WEB_INGEST_TOKEN", "") or "").strip()


def _wa_web_enabled() -> bool:
    return bool(_wa_web_gateway_url())


def _wa_web_request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    base = _wa_web_gateway_url()
    if not base:
        return {"ok": False, "error": "WhatsApp Web gateway not configured"}
    url = base.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    api_key = _wa_web_api_key()
    if api_key:
        headers["X-WA-WEB-KEY"] = api_key
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw or "{}")
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _wa_web_send_text(*, to_phone: str, text: str):
    payload = _wa_web_request("/send", method="POST", payload={"to": to_phone, "text": text})
    if not payload.get("ok"):
        return False, payload.get("error") or "WhatsApp Web send failed", ""
    return True, "", payload.get("message_id") or ""


def _infobip_request(path: str, payload: dict):
    base = _infobip_base_url()
    if not base:
        return False, "Infobip base URL not configured", {}
    api_key = _infobip_api_key()
    if not api_key:
        return False, "Infobip API key not configured", {}
    url = f"{base}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"App {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        return True, "", json.loads(raw or "{}")
    except Exception as e:
        return False, str(e)[:300], {}


def _infobip_send_text(*, to_phone: str, text: str):
    sender = _infobip_sender()
    if not sender:
        return False, "Infobip sender number not configured", ""
    payload = {
        "from": sender,
        "to": to_phone,
        "content": {"type": "text", "text": text},
    }
    ok, err, resp = _infobip_request("/whatsapp/1/message/text", payload)
    if not ok:
        return False, err or "Infobip send failed", ""
    message_id = ""
    if isinstance(resp, dict):
        messages = resp.get("messages") or resp.get("results") or []
        if messages and isinstance(messages, list):
            message_id = (messages[0] or {}).get("messageId") or ""
        if not message_id:
            message_id = resp.get("messageId") or resp.get("message_id") or ""
    return True, "", message_id


def _infobip_send_media(*, to_phone: str, media_url: str, media_type: str, caption: str = "", filename: str = ""):
    sender = _infobip_sender()
    if not sender:
        return False, "Infobip sender number not configured", ""
    if not media_url:
        return False, "Media URL missing", ""
    endpoint = f"/whatsapp/1/message/{media_type}"
    content = {"type": media_type, "mediaUrl": media_url}
    if caption:
        content["caption"] = caption
    if media_type == "document" and filename:
        content["filename"] = filename
    payload = {
        "from": sender,
        "to": to_phone,
        "content": content,
    }
    ok, err, resp = _infobip_request(endpoint, payload)
    if not ok:
        return False, err or "Infobip media send failed", ""
    message_id = ""
    if isinstance(resp, dict):
        messages = resp.get("messages") or resp.get("results") or []
        if messages and isinstance(messages, list):
            message_id = (messages[0] or {}).get("messageId") or ""
        if not message_id:
            message_id = resp.get("messageId") or resp.get("message_id") or ""
    return True, "", message_id


def _wa_send_text(*, to_phone: str, text: str):
    if not _wa_api_ready():
        if _wa_web_enabled():
            return _wa_web_send_text(to_phone=to_phone, text=text)
        return False, "WhatsApp API not configured", ""

    provider = _wa_provider()
    if provider == "infobip":
        return _infobip_send_text(to_phone=to_phone, text=text)

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
            raw = resp.read().decode("utf-8")
            if status >= 300:
                return False, f"HTTP {status}", ""
    except Exception as e:
        return False, str(e)[:300], ""

    message_id = ""
    try:
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            messages = payload.get("messages") or []
            if messages and isinstance(messages, list):
                message_id = (messages[0] or {}).get("id") or ""
    except Exception:
        message_id = ""

    return True, "", message_id


def _wa_send_media(
    *,
    to_phone: str,
    media_id: str = "",
    media_type: str,
    caption: str = "",
    filename: str = "",
    media_url: str = "",
):
    if not _wa_api_ready():
        return False, "WhatsApp API not configured", ""

    if _wa_provider() == "infobip":
        return _infobip_send_media(
            to_phone=to_phone,
            media_url=media_url,
            media_type=media_type,
            caption=caption,
            filename=filename,
        )

    token = getattr(settings, "WA_TOKEN", "")
    phone_id = getattr(settings, "WA_PHONE_NUMBER_ID", "")
    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": media_type,
        media_type: {"id": media_id},
    }

    if caption:
        payload[media_type]["caption"] = caption
    if media_type == "document" and filename:
        payload[media_type]["filename"] = filename

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read().decode("utf-8")
            if status >= 300:
                return False, f"HTTP {status}", ""
    except Exception as e:
        return False, str(e)[:300], ""

    message_id = ""
    try:
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            messages = payload.get("messages") or []
            if messages and isinstance(messages, list):
                message_id = (messages[0] or {}).get("id") or ""
    except Exception:
        message_id = ""

    return True, "", message_id


def _wa_upload_media(*, filename: str, mime: str, data: bytes):
    if not _wa_api_ready():
        return None, "WhatsApp API not configured"

    if _wa_provider() == "infobip":
        return None, "Infobip media upload is not configured yet"

    token = getattr(settings, "WA_TOKEN", "")
    phone_id = getattr(settings, "WA_PHONE_NUMBER_ID", "")
    url = f"https://graph.facebook.com/v20.0/{phone_id}/media"

    boundary = f"----WAForm{uuid4().hex}"
    mime = mime or "application/octet-stream"
    filename = filename or "attachment"

    parts = []
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="messaging_product"\r\n\r\n')
    parts.append(b"whatsapp\r\n")
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
    )
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
    body = b"".join(parts) + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = getattr(resp, "status", 200)
            if status >= 300:
                return None, f"HTTP {status}"
            payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("id"), ""
    except Exception as e:
        return None, str(e)[:300]


def _wa_download_media(media_id: str):
    if not _wa_api_ready() or not media_id:
        return None

    token = getattr(settings, "WA_TOKEN", "")
    meta_url = f"https://graph.facebook.com/v20.0/{media_id}"
    req = urllib.request.Request(meta_url)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    file_url = meta.get("url") or ""
    mime = meta.get("mime_type") or ""
    filename = meta.get("filename") or ""
    if not file_url:
        return None

    req2 = urllib.request.Request(file_url)
    req2.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req2, timeout=30) as resp:
            data = resp.read()
    except Exception:
        return {"url": file_url, "mime": mime, "filename": filename, "data": b""}

    return {"url": file_url, "mime": mime, "filename": filename, "data": data}


def _wa_send_template(*, to_phone: str, template_name: str, language: str = "en_US"):
    if not _wa_api_ready():
        return False, "WhatsApp API not configured", ""

    if _wa_provider() == "infobip":
        return False, "Infobip templates are not configured yet", ""

    token = getattr(settings, "WA_TOKEN", "")
    phone_id = getattr(settings, "WA_PHONE_NUMBER_ID", "")
    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read().decode("utf-8")
            if status >= 300:
                return False, f"HTTP {status}", ""
    except Exception as e:
        return False, str(e)[:300], ""

    message_id = ""
    try:
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            messages = payload.get("messages") or []
            if messages and isinstance(messages, list):
                message_id = (messages[0] or {}).get("id") or ""
    except Exception:
        message_id = ""

    return True, "", message_id


def _should_flag_human(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in ["price", "quote", "cost", "sample", "timeline", "moq", "urgent"])


def _auto_reply_text() -> str:
    return (
        "Thank you for reaching out! We will get back to you shortly."
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
    selected_id = request.GET.get("thread")
    selected_thread = None
    selected_messages = None
    if selected_id:
        try:
            selected_thread = WhatsAppThread.objects.get(pk=int(selected_id))
            selected_messages = selected_thread.messages.order_by("created_at", "id")
        except (WhatsAppThread.DoesNotExist, ValueError):
            selected_thread = None
            selected_messages = None
    if not selected_thread and threads:
        selected_thread = threads[0]
        selected_messages = selected_thread.messages.order_by("created_at", "id")
    context = {
        "threads": threads,
        "wa_provider": _wa_provider(),
        "wa_ready": _wa_api_ready(),
        "wa_web_enabled": _wa_web_enabled(),
        "wa_phone_id": getattr(settings, "WA_PHONE_NUMBER_ID", ""),
        "wa_verify_token_set": bool(getattr(settings, "WA_VERIFY_TOKEN", "")),
        "wa_app_secret_set": bool(getattr(settings, "WA_APP_SECRET", "")),
        "wa_auto_reply_enabled": bool(getattr(settings, "WA_AUTO_REPLY_ENABLED", True)),
        "wa_webhook_url": request.build_absolute_uri("/whatsapp-api/webhook/"),
        "wa_infobip_webhook_url": request.build_absolute_uri("/webhooks/whatsapp/infobip/"),
        "selected_thread": selected_thread,
        "selected_messages": selected_messages,
    }
    return render(request, "crm/whatsapp/inbox.html", context)


@require_POST
@login_required
def wa_start(request):
    raw_phone = (request.POST.get("phone") or "").strip()
    text = (request.POST.get("text") or "").strip()
    template_name = (request.POST.get("template_name") or "").strip()
    template_lang = (request.POST.get("template_lang") or "en_US").strip()
    if not raw_phone or (not text and not template_name):
        return JsonResponse({"ok": False, "error": "Phone and message required"}, status=400)

    to_phone = _digits(raw_phone)
    if not to_phone:
        return JsonResponse({"ok": False, "error": "Invalid phone"}, status=400)

    body = text or f"[template:{template_name}]"
    thread, _ = WhatsAppThread.objects.get_or_create(wa_phone=to_phone)
    msg = WhatsAppMessage.objects.create(
        thread=thread,
        direction="out",
        body=body,
        meta_id=f"out-{uuid4().hex}",
        status="pending",
        created_by=request.user,
    )
    if template_name:
        ok, err, meta_id = _wa_send_template(to_phone=to_phone, template_name=template_name, language=template_lang)
        body = f"[template:{template_name}]"
    else:
        ok, err, meta_id = _wa_send_text(to_phone=to_phone, text=text)
        body = text
    if not ok:
        msg.status = "failed"
        msg.save(update_fields=["status"])
        return JsonResponse({"ok": False, "error": err or "Send failed"}, status=500)

    if body and msg.body != body:
        msg.body = body
    if meta_id:
        msg.meta_id = meta_id
    msg.status = "sent"
    msg.save(update_fields=["meta_id", "status", "body"])
    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])

    return JsonResponse({"ok": True, "thread_id": thread.pk})


@login_required
def wa_thread(request, pk):
    return redirect(f"{reverse('wa_api_inbox')}?thread={pk}")


@login_required
def wa_thread_messages_json(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    after_raw = (request.GET.get("after") or "").strip()
    try:
        after_id = int(after_raw) if after_raw else 0
    except ValueError:
        after_id = 0

    qs = thread.messages.order_by("id")
    if after_id:
        qs = qs.filter(id__gt=after_id)

    msgs = []
    for msg in qs[:50]:
        media_url = msg.media_url or ""
        download_url = media_url
        if msg.media_path:
            media_url = reverse("wa_api_media", args=[msg.pk])
            download_url = f"{media_url}?download=1"
        msgs.append(
            {
                "id": msg.pk,
                "direction": msg.direction,
                "body": msg.body,
                "created_at": timezone.localtime(msg.created_at).strftime("%Y-%m-%d %H:%M"),
                "media_url": media_url,
                "download_url": download_url,
                "media_type": msg.media_type,
                "media_filename": msg.media_filename,
                "is_image": (msg.media_type or "").startswith("image"),
            }
        )

    return JsonResponse({"ok": True, "messages": msgs})


@login_required
def wa_media(request, msg_id: int):
    msg = get_object_or_404(WhatsAppMessage, pk=msg_id)
    if not msg.media_path:
        raise Http404("No media")
    try:
        f = default_storage.open(msg.media_path, "rb")
    except Exception:
        raise Http404("Missing media")
    content_type = msg.media_type or mimetypes.guess_type(msg.media_filename or msg.media_path)[0] or "application/octet-stream"
    resp = FileResponse(f, content_type=content_type)
    filename = msg.media_filename or os.path.basename(msg.media_path) or "attachment"
    filename = filename.replace('"', "'")
    disposition = "attachment" if request.GET.get("download") == "1" else "inline"
    resp["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_POST
@login_required
def wa_send(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    text = (request.POST.get("text") or "").strip()
    upload = request.FILES.get("file")
    if not text and not upload:
        return JsonResponse({"ok": False, "error": "Message or file required"}, status=400)

    media_path = ""
    media_type = ""
    media_filename = ""
    media_url = ""
    media_id = ""

    if upload:
        if not _wa_api_ready() and _wa_web_enabled():
            return JsonResponse(
                {"ok": False, "error": "Attachments are not supported in WhatsApp Web mode yet."},
                status=400,
            )
        media_type = upload.content_type or mimetypes.guess_type(upload.name or "")[0] or ""
        media_filename = upload.name or "attachment"
        raw = upload.read()
        safe_phone = (thread.wa_phone or "chat").replace("/", "_")
        fname = f"whatsapp_api/outbound/{safe_phone}/{uuid4().hex}_{media_filename}"
        media_path = default_storage.save(fname, ContentFile(raw))
        media_url = default_storage.url(media_path) if media_path else ""

        if media_type.startswith("image/"):
            send_type = "image"
        elif media_type.startswith("video/"):
            send_type = "video"
        elif media_type.startswith("audio/"):
            send_type = "audio"
        else:
            send_type = "document"

        if _wa_provider() != "infobip":
            media_id, err = _wa_upload_media(filename=media_filename, mime=media_type, data=raw)
            if not media_id:
                return JsonResponse({"ok": False, "error": err or "Media upload failed"}, status=500)
    msg = WhatsAppMessage.objects.create(
        thread=thread,
        direction="out",
        body=text,
        meta_id=f"out-{uuid4().hex}",
        status="pending",
        media_path=media_path,
        media_url=media_url,
        media_type=media_type,
        media_filename=media_filename,
        created_by=request.user,
    )
    if upload:
        ok, err, meta_id = _wa_send_media(
            to_phone=thread.wa_phone,
            media_id=media_id,
            media_type=send_type,
            caption=text,
            filename=media_filename,
            media_url=_absolute_media_url(media_url),
        )
    else:
        ok, err, meta_id = _wa_send_text(to_phone=thread.wa_phone, text=text)

    if not ok:
        msg.status = "failed"
        msg.save(update_fields=["status"])
        return JsonResponse({"ok": False, "error": err}, status=500)

    if meta_id:
        msg.meta_id = meta_id
    msg.status = "sent"
    msg.save(update_fields=["meta_id", "status"])

    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])

    return JsonResponse({"ok": True})


@login_required
def wa_web_status(request):
    payload = _wa_web_request("/status")
    return JsonResponse(payload)


@login_required
def wa_web_qr(request):
    payload = _wa_web_request("/qr")
    return JsonResponse(payload)


@csrf_exempt
def wa_web_ingest(request):
    if request.method != "POST":
        return HttpResponseForbidden("forbidden")
    expected = _wa_web_ingest_token()
    if expected:
        token = request.headers.get("X-WA-WEB-KEY") or request.GET.get("key") or ""
        if token != expected:
            return HttpResponseForbidden("forbidden")

    try:
        data = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        return HttpResponse("ok")

    wa_from = _digits(data.get("from") or "")
    if not wa_from:
        return HttpResponse("ok")

    direction = (data.get("direction") or "in").lower()
    body = (data.get("body") or "").strip()
    name = (data.get("name") or "").strip()
    meta_id = (data.get("meta_id") or "").strip() or f"web-{uuid4().hex}"

    thread, _ = WhatsAppThread.objects.get_or_create(wa_phone=wa_from)
    if name and not (thread.wa_name or ""):
        thread.wa_name = name

    if not getattr(thread, "lead_id", None):
        lead = Lead.objects.filter(phone__icontains=wa_from).order_by("-id").first()
        if lead:
            thread.lead = lead

    msg, created = WhatsAppMessage.objects.get_or_create(
        thread=thread,
        meta_id=meta_id,
        defaults={
            "direction": direction,
            "body": body,
            "status": "received" if direction == "in" else "sent",
        },
    )
    if not created and body and not msg.body:
        msg.body = body
        msg.save(update_fields=["body"])

    thread.last_message_at = timezone.now()
    if hasattr(thread, "needs_human"):
        thread.needs_human = _should_flag_human(body)
    thread.save()

    # Optional auto reply in web mode
    auto_reply_enabled = bool(getattr(settings, "WA_AUTO_REPLY_ENABLED", True))
    if direction == "in" and auto_reply_enabled and getattr(thread, "ai_enabled", True) and not getattr(thread, "needs_human", False):
        if _can_auto_reply(thread):
            reply_text = _auto_reply_text()
            msg = WhatsAppMessage.objects.create(
                thread=thread,
                direction="out",
                body=reply_text,
                meta_id=f"out-{uuid4().hex}",
                status="pending",
            )
            ok2, _, meta_id = _wa_web_send_text(to_phone=thread.wa_phone, text=reply_text)
            if not ok2:
                msg.status = "failed"
                msg.save(update_fields=["status"])
            else:
                if meta_id:
                    msg.meta_id = meta_id
                msg.status = "sent"
                msg.save(update_fields=["meta_id", "status"])
                if hasattr(thread, "last_auto_reply_at"):
                    thread.last_auto_reply_at = timezone.now()
                    thread.save(update_fields=["last_auto_reply_at"])

    return HttpResponse("ok")


@require_POST
@login_required
def wa_toggle_ai(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    if not hasattr(thread, "ai_enabled"):
        return JsonResponse({"ok": False, "error": "AI flag not available"}, status=400)
    thread.ai_enabled = not thread.ai_enabled
    thread.save(update_fields=["ai_enabled"])
    return JsonResponse({"ok": True, "ai_enabled": thread.ai_enabled})


@require_POST
@login_required
def wa_send_ai_draft(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)

    draft = (request.POST.get("draft") or "").strip()
    if not draft:
        return JsonResponse({"ok": False, "error": "Draft is empty"}, status=400)

    if hasattr(thread, "ai_draft"):
        thread.ai_draft = draft

    msg = WhatsAppMessage.objects.create(
        thread=thread,
        direction="out",
        body=draft,
        meta_id=f"out-{uuid4().hex}",
        status="pending",
        created_by=request.user,
    )
    ok, err, meta_id = _wa_send_text(to_phone=thread.wa_phone, text=draft)
    if not ok:
        msg.status = "failed"
        msg.save(update_fields=["status"])
        return JsonResponse({"ok": False, "error": err or "Send failed"}, status=500)

    if meta_id:
        msg.meta_id = meta_id
    msg.status = "sent"
    msg.save(update_fields=["meta_id", "status"])

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


@require_POST
@login_required
def wa_follow_up(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    days_raw = (request.POST.get("days") or "1").strip()
    note = (request.POST.get("note") or "").strip()
    try:
        days = int(days_raw)
    except ValueError:
        days = 1

    if days < 1 or days > 30:
        return JsonResponse({"ok": False, "error": "Days must be between 1 and 30"}, status=400)

    follow_date = timezone.localdate() + timedelta(days=days)
    follow_start = datetime.combine(follow_date, time(hour=10, minute=0))
    follow_start = timezone.make_aware(follow_start, timezone.get_current_timezone())
    follow_end = follow_start + timedelta(minutes=30)

    lead = thread.lead
    customer = lead.customer if lead and lead.customer_id else None
    title_name = thread.wa_name or thread.display_phone or thread.wa_phone
    note_text = "WhatsApp follow-up scheduled from CRM."
    if note:
        note_text = f"{note_text}\n{note}"

    Event.objects.create(
        title=f"WhatsApp follow-up: {title_name}",
        start_datetime=follow_start,
        end_datetime=follow_end,
        event_type="follow_up",
        priority="medium",
        status="planned",
        note=note_text,
        lead=lead,
        customer=customer,
    )

    if lead:
        lead.next_followup = follow_date
        lead.save(update_fields=["next_followup"])

    return JsonResponse({"ok": True, "date": follow_date.isoformat()})


def _infobip_iter_items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return results
        messages = payload.get("messages")
        if isinstance(messages, list):
            return messages
        return [payload]
    return []


def _infobip_status_to_local(status_obj) -> str:
    text = ""
    if isinstance(status_obj, dict):
        text = " ".join(
            [
                str(status_obj.get("name", "")),
                str(status_obj.get("groupName", "")),
                str(status_obj.get("description", "")),
            ]
        )
    else:
        text = str(status_obj or "")
    text = text.upper()
    if "READ" in text:
        return "read"
    if "DELIVERED" in text:
        return "delivered"
    if "SENT" in text or "SUBMITTED" in text:
        return "sent"
    if "FAILED" in text or "REJECT" in text or "UNDELIVER" in text:
        return "failed"
    return "sent"


def _process_infobip_payload(payload: dict) -> int:
    processed = 0
    auto_reply_enabled = bool(getattr(settings, "WA_AUTO_REPLY_ENABLED", True))

    for item in _infobip_iter_items(payload):
        if not isinstance(item, dict):
            continue

        status_obj = item.get("status")
        message_block = item.get("message") or item.get("content") or {}

        if status_obj and not message_block:
            msg_id = item.get("messageId") or item.get("message_id") or item.get("id") or ""
            if msg_id:
                status_val = _infobip_status_to_local(status_obj)
                WhatsAppMessage.objects.filter(meta_id=msg_id).update(status=status_val)
                processed += 1
            continue

        wa_from = _digits(item.get("from") or "")
        if not wa_from:
            continue

        name = (
            (item.get("fromName") or "")
            or (item.get("senderName") or "")
            or (item.get("contact") or {}).get("name", "")
        )

        msg_type = (message_block.get("type") or item.get("type") or "text").lower()
        body = ""
        media_url = ""
        media_type = ""
        media_filename = ""

        if msg_type == "text":
            text_val = message_block.get("text")
            if isinstance(text_val, dict):
                body = text_val.get("text") or ""
            else:
                body = text_val or ""
        else:
            body = message_block.get("caption") or ""
            media_url = (
                message_block.get("url")
                or message_block.get("mediaUrl")
                or message_block.get("media_url")
                or ""
            )
            media_filename = message_block.get("filename") or message_block.get("fileName") or ""
            media_type = msg_type

        if msg_type and not body:
            body = f"[{msg_type}]"

        msg_id = item.get("messageId") or item.get("message_id") or item.get("id") or f"in-{uuid4().hex}"

        thread, _ = WhatsAppThread.objects.get_or_create(wa_phone=wa_from)
        if name and not (thread.wa_name or ""):
            thread.wa_name = name

        if not getattr(thread, "lead_id", None):
            lead = Lead.objects.filter(phone__icontains=wa_from).order_by("-id").first()
            if lead:
                thread.lead = lead

        msg, created = WhatsAppMessage.objects.get_or_create(
            thread=thread,
            meta_id=msg_id,
            defaults={
                "direction": "in",
                "body": body,
                "status": "received",
                "media_url": media_url,
                "media_type": media_type,
                "media_filename": media_filename,
            },
        )
        if not created:
            update_fields = []
            if body and not msg.body:
                msg.body = body
                update_fields.append("body")
            if media_url and not msg.media_url:
                msg.media_url = media_url
                update_fields.append("media_url")
            if media_type and not msg.media_type:
                msg.media_type = media_type
                update_fields.append("media_type")
            if media_filename and not msg.media_filename:
                msg.media_filename = media_filename
                update_fields.append("media_filename")
            if update_fields:
                msg.save(update_fields=update_fields)

        thread.last_message_at = timezone.now()
        if hasattr(thread, "needs_human"):
            thread.needs_human = _should_flag_human(body)

        if auto_reply_enabled and getattr(thread, "ai_enabled", True) and not getattr(thread, "needs_human", False):
            if _wa_api_ready() and _can_auto_reply(thread):
                reply_text = _auto_reply_text()
                out_msg = WhatsAppMessage.objects.create(
                    thread=thread,
                    direction="out",
                    body=reply_text,
                    meta_id=f"out-{uuid4().hex}",
                    status="pending",
                )
                ok2, _, meta_id = _wa_send_text(to_phone=thread.wa_phone, text=reply_text)
                if not ok2:
                    out_msg.status = "failed"
                    out_msg.save(update_fields=["status"])
                else:
                    if meta_id:
                        out_msg.meta_id = meta_id
                    out_msg.status = "sent"
                    out_msg.save(update_fields=["meta_id", "status"])
                    if hasattr(thread, "last_auto_reply_at"):
                        thread.last_auto_reply_at = timezone.now()

        thread.save()
        processed += 1

    return processed


def _process_infobip_event(event_id: int):
    event = WhatsAppWebhookEvent.objects.filter(pk=event_id).first()
    if not event:
        return
    if event.status not in {"new", "failed"}:
        return
    event.status = "processing"
    event.save(update_fields=["status"])
    try:
        _process_infobip_payload(event.raw_payload or {})
        event.status = "processed"
        event.processed_at = timezone.now()
        event.error_message = ""
    except Exception as e:
        event.status = "failed"
        event.error_message = str(e)[:500]
    event.save(update_fields=["status", "processed_at", "error_message"])


def _queue_infobip_event(event_id: int):
    worker = threading.Thread(target=_process_infobip_event, args=(event_id,), daemon=True)
    worker.start()


@csrf_exempt
def wa_infobip_webhook(request):
    if request.method != "POST":
        return HttpResponseForbidden("forbidden")

    expected = getattr(settings, "WHATSAPP_INFOBIP_WEBHOOK_TOKEN", "")
    if expected:
        token = request.headers.get("X-Webhook-Token") or request.headers.get("X-Infobip-Token") or request.GET.get("token") or ""
        if token != expected:
            return HttpResponseForbidden("forbidden")

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        payload = {}

    event = WhatsAppWebhookEvent.objects.create(
        provider="infobip",
        raw_payload=payload,
        status="new",
    )
    _queue_infobip_event(event.pk)
    return HttpResponse("ok")


@login_required
def wa_infobip_events(request):
    events = WhatsAppWebhookEvent.objects.filter(provider="infobip").order_by("-received_at")[:50]
    return render(
        request,
        "crm/whatsapp/infobip_events.html",
        {"events": events},
    )


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

        auto_reply_enabled = bool(getattr(settings, "WA_AUTO_REPLY_ENABLED", True))

        for m in messages:
            wa_from = _digits(m.get("from") or "")
            msg_type = (m.get("type") or "").lower()
            body = ""
            media_id = ""
            media_mime = ""
            media_filename = ""
            if msg_type == "text":
                body = (m.get("text") or {}).get("body") or ""
            elif msg_type in {"image", "document", "video", "audio"}:
                media = m.get(msg_type) or {}
                media_id = media.get("id") or ""
                media_mime = media.get("mime_type") or ""
                media_filename = media.get("filename") or ""
                body = media.get("caption") or ""
            if msg_type and not body:
                body = f"[{msg_type}]"

            msg_id = m.get("id") or f"fallback-{uuid4()}"

            if not wa_from:
                continue

            thread, _ = WhatsAppThread.objects.get_or_create(wa_phone=wa_from)

            if contact_name and not (thread.wa_name or ""):
                thread.wa_name = contact_name

            if not getattr(thread, "lead_id", None):
                lead = Lead.objects.filter(phone__icontains=wa_from).order_by("-id").first()
                if lead:
                    thread.lead = lead

            saved_media_url = ""
            saved_media_path = ""
            saved_media_type = media_mime
            saved_media_filename = media_filename

            if media_id:
                media_data = _wa_download_media(media_id)
                if media_data:
                    if media_data.get("data"):
                        ext = ""
                        if saved_media_filename:
                            ext = os.path.splitext(saved_media_filename)[1]
                        if not ext and media_data.get("mime"):
                            ext = mimetypes.guess_extension(media_data.get("mime")) or ""
                        safe_phone = wa_from or "chat"
                        fname = f"whatsapp_api/{safe_phone}/{media_id}{ext}"
                        saved_path = default_storage.save(fname, ContentFile(media_data.get("data")))
                        saved_media_path = saved_path
                        saved_media_url = default_storage.url(saved_path)
                        saved_media_type = media_data.get("mime") or saved_media_type
                        if not saved_media_filename:
                            saved_media_filename = media_data.get("filename") or os.path.basename(saved_path)
                    else:
                        saved_media_url = media_data.get("url") or ""
                        saved_media_type = media_data.get("mime") or saved_media_type
                        if not saved_media_filename:
                            saved_media_filename = media_data.get("filename") or ""

            msg, created = WhatsAppMessage.objects.get_or_create(
                thread=thread,
                meta_id=msg_id,
                defaults={
                    "direction": "in",
                    "body": body,
                    "status": "received",
                    "media_url": saved_media_url,
                    "media_type": saved_media_type,
                    "media_path": saved_media_path,
                    "media_filename": saved_media_filename,
                },
            )
            if not created:
                update_fields = []
                if body and not msg.body:
                    msg.body = body
                    update_fields.append("body")
                if saved_media_url and not msg.media_url:
                    msg.media_url = saved_media_url
                    update_fields.append("media_url")
                if saved_media_type and not msg.media_type:
                    msg.media_type = saved_media_type
                    update_fields.append("media_type")
                if saved_media_path and not msg.media_path:
                    msg.media_path = saved_media_path
                    update_fields.append("media_path")
                if saved_media_filename and not msg.media_filename:
                    msg.media_filename = saved_media_filename
                    update_fields.append("media_filename")
                if update_fields:
                    msg.save(update_fields=update_fields)

            thread.last_message_at = timezone.now()

            if hasattr(thread, "needs_human"):
                thread.needs_human = _should_flag_human(body)

            if auto_reply_enabled and getattr(thread, "ai_enabled", True) and not getattr(thread, "needs_human", False):
                if _wa_api_ready() and _can_auto_reply(thread):
                    reply_text = _auto_reply_text()
                    msg = WhatsAppMessage.objects.create(
                        thread=thread,
                        direction="out",
                        body=reply_text,
                        meta_id=f"out-{uuid4().hex}",
                        status="pending",
                    )
                    ok2, _, meta_id = _wa_send_text(to_phone=thread.wa_phone, text=reply_text)
                    if not ok2:
                        msg.status = "failed"
                        msg.save(update_fields=["status"])
                    else:
                        if meta_id:
                            msg.meta_id = meta_id
                        msg.status = "sent"
                        msg.save(update_fields=["meta_id", "status"])
                        if hasattr(thread, "last_auto_reply_at"):
                            thread.last_auto_reply_at = timezone.now()

            thread.save()

        return HttpResponse("ok")

    return HttpResponseForbidden("forbidden")
