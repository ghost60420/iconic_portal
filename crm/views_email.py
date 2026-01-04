from datetime import timedelta
import subprocess

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from crm.models_email import EmailThread, EmailMessage


def superuser_only(user):
    return user.is_superuser


@login_required
@user_passes_test(superuser_only)
def email_sync_dashboard(request):
    now = timezone.now()
    last_24h = now - timedelta(hours=24)

    q = (request.GET.get("q") or "").strip()
    label = (request.GET.get("label") or "").strip()   # lead / info / ""
    flag = (request.GET.get("flag") or "").strip()     # form / candidate / ""

    msgs = EmailMessage.objects.select_related("thread").order_by("-created_at")

    if label:
        msgs = msgs.filter(thread__label=label)

    if flag == "form":
        msgs = msgs.filter(is_form_entry=True)
    elif flag == "candidate":
        msgs = msgs.filter(is_lead_candidate=True)

    if q:
        msgs = msgs.filter(
            Q(subject__icontains=q)
            | Q(from_email__icontains=q)
            | Q(from_name__icontains=q)
            | Q(body_text__icontains=q)
        )

    grid = msgs[:200]

    threads = EmailThread.objects.order_by("-last_message_at")[:50]

    stats = {
        "total_24h": EmailMessage.objects.filter(created_at__gte=last_24h).count(),
        "forms_24h": EmailMessage.objects.filter(created_at__gte=last_24h, is_form_entry=True).count(),
        "candidates_24h": EmailMessage.objects.filter(created_at__gte=last_24h, is_lead_candidate=True).count(),
    }

    return render(
        request,
        "crm/email_sync/dashboard.html",
        {
            "threads": threads,
            "grid": grid,
            "stats": stats,
            "q": q,
            "label": label,
            "flag": flag,
        },
    )


@require_POST
@login_required
@user_passes_test(superuser_only)
def email_sync_run(request):
    try:
        subprocess.run(
            ["python3", "manage.py", "sync_inboxes", "--limit", "50"],
            check=True,
            cwd=str(settings.BASE_DIR),
        )
        messages.success(request, "Email sync finished.")
    except Exception as e:
        messages.error(request, f"Email sync failed: {str(e)}")

    return redirect("email_sync_dashboard")