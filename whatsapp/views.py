import json
import base64
import os
import mimetypes
from uuid import uuid4
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from crm.permissions import require_access
from whatsapp.models import (
    WhatsAppAccount,
    WhatsAppThread,
    WhatsAppMessage,
    WhatsAppAutomationRule,
    WhatsAppSendQueue,
    DoNotContactPhone,
    WhatsAppEventLog,
)
from whatsapp.services import client as wa_client
from whatsapp.utils.phones import normalize_phone
from whatsapp.utils.limits import is_dnc
from whatsapp.utils.automation import run_inbound_automation
from whatsapp.utils.linking import link_existing_lead, create_lead_from_thread
from whatsapp.utils.templates import render_template


def _flag_enabled(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _get_account():
    phone = getattr(settings, "WHATSAPP_PHONE_NUMBER", "6045006009")
    account, _ = WhatsAppAccount.objects.get_or_create(phone_number=phone)
    return account


@login_required
@require_access("can_whatsapp")
def inbox(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return render(request, "whatsapp/disabled.html")

    account = _get_account()
    threads = WhatsAppThread.objects.filter(account=account, is_archived=False).order_by("-last_message_at", "-id")[:300]
    selected_id = request.GET.get("thread")
    selected_thread = None
    selected_messages = None
    if selected_id:
        try:
            selected_thread = WhatsAppThread.objects.get(pk=int(selected_id), account=account)
            selected_messages = selected_thread.messages.order_by("created_at", "id")
        except (WhatsAppThread.DoesNotExist, ValueError):
            selected_thread = None
            selected_messages = None
    if not selected_thread and threads:
        selected_thread = threads[0]
        selected_messages = selected_thread.messages.order_by("created_at", "id")
    webhook_url = request.build_absolute_uri("/whatsapp/webhook/")
    context = {
        "threads": threads,
        "account": account,
        "webhook_url": webhook_url,
        "selected_thread": selected_thread,
        "selected_messages": selected_messages,
    }
    return render(request, "whatsapp/inbox.html", context)


@login_required
@require_access("can_whatsapp")
def thread_view(request, pk):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return render(request, "whatsapp/disabled.html")

    thread = get_object_or_404(WhatsAppThread, pk=pk)
    messages_qs = thread.messages.order_by("created_at", "id")
    return render(request, "whatsapp/thread.html", {"thread": thread, "messages": messages_qs})


@login_required
@require_access("can_whatsapp")
@require_POST
def start_chat(request):
    if not _flag_enabled("WHATSAPP_OUTBOUND_ENABLED"):
        return JsonResponse({"ok": False, "error": "Outbound disabled"}, status=403)

    raw_phone = (request.POST.get("phone") or "").strip()
    name = (request.POST.get("name") or "").strip()
    text = (request.POST.get("text") or "").strip()
    phone = normalize_phone(raw_phone)
    if not phone:
        return JsonResponse({"ok": False, "error": "Phone required"}, status=400)

    if is_dnc(phone):
        return JsonResponse({"ok": False, "error": "Do Not Contact"}, status=403)

    account = _get_account()
    wa_chat_id = f"{phone}@c.us"
    thread, _ = WhatsAppThread.objects.get_or_create(
        account=account,
        wa_chat_id=wa_chat_id,
        defaults={
            "contact_phone": phone,
            "contact_name": name,
        },
    )

    if name and not thread.contact_name:
        thread.contact_name = name

    if not thread.contact_phone:
        thread.contact_phone = phone

    if not thread.linked_lead:
        lead = link_existing_lead(phone)
        if lead:
            thread.linked_lead = lead

    thread.save()

    if text:
        body = render_template(text, lead=thread.linked_lead)
        WhatsAppSendQueue.objects.create(
            account=thread.account,
            thread=thread,
            message_body=body,
            scheduled_at=timezone.now(),
        )

    return JsonResponse({"ok": True, "thread_id": thread.pk})


@login_required
@require_access("can_whatsapp")
def settings_view(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return render(request, "whatsapp/disabled.html")

    account = _get_account()
    webhook_url = request.build_absolute_uri("/whatsapp/webhook/")
    status = {}
    qr = {}
    try:
        status = wa_client.get_status()
    except Exception as exc:
        status = {"ok": False, "error": str(exc)[:200]}

    if status.get("status") == "qr_required":
        try:
            qr = wa_client.get_qr()
        except Exception:
            qr = {}

    qr_data = qr.get("qr") or ""
    show_qr = status.get("status") == "qr_required" or bool(qr_data)
    qr_ts = int(timezone.now().timestamp())

    return render(
        request,
        "whatsapp/settings.html",
        {
            "account": account,
            "status": status,
            "qr": qr,
            "qr_data": qr_data,
            "webhook_url": webhook_url,
            "show_qr": show_qr,
            "qr_ts": qr_ts,
        },
    )


@login_required
@require_access("can_whatsapp")
def qr_image(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return HttpResponse("disabled", status=404)

    try:
        qr = wa_client.get_qr()
    except Exception:
        return HttpResponse("no qr", status=404)

    data = qr.get("qr") or ""
    if not data or "base64," not in data:
        return HttpResponse("no qr", status=404)

    try:
        encoded = data.split("base64,", 1)[1]
        img = base64.b64decode(encoded)
    except Exception:
        return HttpResponse("bad qr", status=400)

    resp = HttpResponse(img, content_type="image/png")
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp


@login_required
@require_access("can_whatsapp")
def status_json(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return JsonResponse({"ok": False, "error": "disabled"}, status=403)
    try:
        status = wa_client.get_status()
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)[:200]}, status=500)
    return JsonResponse(status)


@login_required
@require_access("can_whatsapp")
@require_POST
def logout_view(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return JsonResponse({"ok": False, "error": "Disabled"}, status=403)

    account = _get_account()
    try:
        wa_client.logout()
        account.status = "qr_required"
        account.save(update_fields=["status"])
        WhatsAppEventLog.objects.create(account=account, event="logout", level="info")
        return JsonResponse({"ok": True})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)[:200]}, status=500)


@login_required
@require_access("can_whatsapp")
@require_POST
def refresh_qr(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return JsonResponse({"ok": False, "error": "Disabled"}, status=403)

    try:
        wa_client.refresh()
        return JsonResponse({"ok": True})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)[:200]}, status=500)


@login_required
@require_access("can_whatsapp")
def automation_view(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return render(request, "whatsapp/disabled.html")

    rules = WhatsAppAutomationRule.objects.all().order_by("name")
    return render(request, "whatsapp/automation.html", {"rules": rules})


@login_required
@require_access("can_whatsapp")
@require_POST
def enqueue_message(request, pk):
    if not _flag_enabled("WHATSAPP_OUTBOUND_ENABLED"):
        return JsonResponse({"ok": False, "error": "Outbound disabled"}, status=403)

    thread = get_object_or_404(WhatsAppThread, pk=pk)
    text = (request.POST.get("text") or "").strip()
    if not text:
        return JsonResponse({"ok": False, "error": "Message is empty"}, status=400)

    if is_dnc(thread.contact_phone):
        return JsonResponse({"ok": False, "error": "Do Not Contact"}, status=403)

    body = render_template(text, lead=thread.linked_lead)
    WhatsAppSendQueue.objects.create(
        account=thread.account,
        thread=thread,
        message_body=body,
        scheduled_at=timezone.now(),
    )

    return JsonResponse({"ok": True})


@login_required
@require_access("can_whatsapp")
@require_POST
def schedule_followup(request, pk):
    if not _flag_enabled("WHATSAPP_OUTBOUND_ENABLED"):
        return JsonResponse({"ok": False, "error": "Outbound disabled"}, status=403)

    thread = get_object_or_404(WhatsAppThread, pk=pk)
    text = (request.POST.get("text") or "").strip()
    delay_days = int(request.POST.get("delay_days") or "1")

    if not text:
        return JsonResponse({"ok": False, "error": "Message required"}, status=400)

    if is_dnc(thread.contact_phone):
        return JsonResponse({"ok": False, "error": "Do Not Contact"}, status=403)

    scheduled = timezone.now() + timedelta(days=delay_days)
    body = render_template(text, lead=thread.linked_lead)

    WhatsAppSendQueue.objects.create(
        account=thread.account,
        thread=thread,
        message_body=body,
        scheduled_at=scheduled,
    )

    return JsonResponse({"ok": True})


@login_required
@require_access("can_whatsapp")
@require_POST
def toggle_automation(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    thread.automation_enabled = not thread.automation_enabled
    thread.save(update_fields=["automation_enabled"])
    return JsonResponse({"ok": True, "automation_enabled": thread.automation_enabled})


@login_required
@require_access("can_whatsapp")
@require_POST
def create_lead(request, pk):
    thread = get_object_or_404(WhatsAppThread, pk=pk)
    if thread.linked_lead:
        return JsonResponse({"ok": True, "lead_id": thread.linked_lead.pk})

    name = (request.POST.get("name") or "").strip()
    lead = create_lead_from_thread(thread, name=name)
    return JsonResponse({"ok": True, "lead_id": lead.pk})


@csrf_exempt
def webhook(request):
    if not _flag_enabled("WHATSAPP_ENABLED"):
        return HttpResponse("disabled")

    secret = (getattr(settings, "WHATSAPP_WEBHOOK_SECRET", "") or "").strip()
    incoming = (request.headers.get("X-WhatsApp-Secret", "") or "").strip()
    if secret and incoming != secret:
        return HttpResponseForbidden("bad secret")

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        return HttpResponse("ok")

    account = _get_account()

    event_type = payload.get("event") or "message"
    if event_type in {"session_connected", "session_disconnected", "qr_required"}:
        account.status = "connected" if event_type == "session_connected" else "qr_required"
        account.last_seen_at = timezone.now()
        account.save(update_fields=["status", "last_seen_at"])
        WhatsAppEventLog.objects.create(account=account, event=event_type, payload_json=payload)
        return HttpResponse("ok")

    if event_type == "message":
        chat_id = payload.get("chat_id") or ""
        from_phone = normalize_phone(payload.get("from") or "")
        body = payload.get("body") or ""
        media_url = payload.get("media_url") or ""
        media_type = payload.get("media_type") or ""
        media_base64 = payload.get("media_base64") or ""
        media_mime = payload.get("media_mime") or media_type
        media_filename = payload.get("media_filename") or ""
        wa_message_id = payload.get("message_id") or ""
        contact_name = payload.get("contact_name") or ""

        if not chat_id:
            return HttpResponse("ok")

        thread, _ = WhatsAppThread.objects.get_or_create(
            account=account,
            wa_chat_id=chat_id,
            defaults={"contact_phone": from_phone, "contact_name": contact_name},
        )

        if contact_name and not thread.contact_name:
            thread.contact_name = contact_name

        if from_phone and not thread.contact_phone:
            thread.contact_phone = from_phone

        if not thread.linked_lead:
            lead = link_existing_lead(from_phone)
            if lead:
                thread.linked_lead = lead

        saved_media_url = media_url
        saved_media_type = media_type or media_mime

        if media_base64:
            try:
                data = base64.b64decode(media_base64)
                ext = ""
                if media_filename:
                    ext = os.path.splitext(media_filename)[1]
                if not ext and media_mime:
                    ext = mimetypes.guess_extension(media_mime) or ""
                safe_chat = chat_id.replace("@", "_").replace("/", "_")
                fname = f"whatsapp/{safe_chat}/{wa_message_id or uuid4().hex}{ext}"
                saved_path = default_storage.save(fname, ContentFile(data))
                saved_media_url = default_storage.url(saved_path)
                saved_media_type = media_mime or media_type
            except Exception:
                saved_media_url = media_url

        msg, created = WhatsAppMessage.objects.get_or_create(
            thread=thread,
            wa_message_id=wa_message_id or f"in-{timezone.now().timestamp()}",
            defaults={
                "direction": "inbound",
                "body": body,
                "media_url": saved_media_url,
                "media_type": saved_media_type,
                "status": "delivered",
                "received_at": timezone.now(),
            },
        )
        if not created and saved_media_url and not msg.media_url:
            msg.media_url = saved_media_url
            msg.media_type = saved_media_type
            msg.save(update_fields=["media_url", "media_type"])

        thread.last_message_at = timezone.now()
        thread.save()

        account.status = "connected"
        account.last_seen_at = timezone.now()
        account.save(update_fields=["status", "last_seen_at"])

        inbound_text = (body or "").lower()
        if any(term in inbound_text for term in ["stop", "unsubscribe", "remove", "opt out", "do not contact"]):
            if from_phone:
                DoNotContactPhone.objects.get_or_create(phone=from_phone, defaults={"reason": "opt-out"})
            thread.automation_enabled = False
            thread.save(update_fields=["automation_enabled"])
            WhatsAppEventLog.objects.create(
                account=account,
                thread=thread,
                event="opt_out",
                level="info",
                payload_json={"from": from_phone, "body": body},
            )
            return HttpResponse("ok")

        if thread.automation_enabled and _flag_enabled("WHATSAPP_AUTOMATION_ENABLED"):
            run_inbound_automation(thread, body, lead=thread.linked_lead)

        WhatsAppEventLog.objects.create(account=account, thread=thread, event="message_in", payload_json=payload)
        return HttpResponse("ok")

    return HttpResponse("ok")
