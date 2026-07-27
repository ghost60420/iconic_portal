# crm/views_access.py

import json
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms_access import UserAccessForm
from .models import CRMAuditLog
from .models_access import UserAccess

User = get_user_model()
logger = logging.getLogger(__name__)

LIBRARY_PERMISSION_FIELDS = (
    "can_library",
    "can_add_library",
    "can_edit_library",
    "can_upload_library_images",
    "can_remove_library_images",
    "can_archive_library",
    "can_use_library_presentation",
    "can_view_library_pricing",
)

LIBRARY_PERMISSION_FIELD_GROUP = {
    "title": "Library Permissions",
    "fields": list(LIBRARY_PERMISSION_FIELDS),
    "note": "Controls access to Products, Fabrics, Accessories, Trims, Threads, images, Presentation Mode, and internal pricing.",
}


def is_admin_user(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    try:
        access = getattr(user, "access", None)
    except Exception:
        access = None
    return bool(access and getattr(access, "can_view_ceo_tools", False))


def _library_permission_snapshot(access):
    return {field_name: bool(getattr(access, field_name, False)) for field_name in LIBRARY_PERMISSION_FIELDS}


def _write_library_permission_audit(actor, target_user, before, after):
    if before == after:
        return
    try:
        CRMAuditLog.objects.create(
            actor=actor if actor and actor.is_authenticated else None,
            module="library_permissions",
            record_id=str(target_user.pk),
            record_label=target_user.get_username()[:220],
            action_type=CRMAuditLog.ACTION_UPDATED,
            field_name="library_permissions",
            previous_value=json.dumps(before, sort_keys=True),
            new_value=json.dumps(after, sort_keys=True),
            target_url=reverse("access_edit", args=[target_user.pk]),
        )
    except Exception:
        logger.exception("Library permission audit write failed; permission save was preserved")


@login_required
@user_passes_test(is_admin_user, login_url="/accounts/login/", redirect_field_name="next")
def access_list(request):
    error_form = None
    error_user_id = None

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        target_user = get_object_or_404(User, id=user_id)
        access, _ = UserAccess.objects.get_or_create(user=target_user)

        if target_user.is_superuser and not request.user.is_superuser:
            return HttpResponseForbidden("No access")

        original_ceo_tools_access = access.can_view_ceo_tools
        before_library_permissions = _library_permission_snapshot(access)
        form = UserAccessForm(
            request.POST,
            instance=access,
            prefix=f"user_{target_user.id}",
            can_manage_ceo_tools=request.user.is_superuser,
        )
        if form.is_valid():
            obj = form.save(commit=False)
            if not request.user.is_superuser:
                obj.can_view_ceo_tools = original_ceo_tools_access
            if obj.role == UserAccess.ROLE_BD:
                obj.can_accounting_ca = False
            obj.save()
            _write_library_permission_audit(
                request.user,
                target_user,
                before_library_permissions,
                _library_permission_snapshot(obj),
            )
            messages.success(request, f"Access updated for {target_user.username}.")
            return redirect("access_list")

        messages.error(request, "Please fix the errors and try again.")
        error_form = form
        error_user_id = target_user.id

    q = (request.GET.get("q") or "").strip()
    users_qs = User.objects.all().order_by("username")
    if q:
        users_qs = users_qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
    users = users_qs.select_related("access")

    field_groups = [
        {"title": "Core", "fields": ["can_leads", "can_opportunities", "can_customers", "can_calendar"], "note": ""},
        LIBRARY_PERMISSION_FIELD_GROUP,
        {"title": "Operations", "fields": ["can_inventory", "can_production", "can_shipping"], "note": ""},
        {"title": "Engagement", "fields": ["can_ai", "can_marketing", "can_whatsapp"], "note": ""},
        {"title": "Costing", "fields": ["can_costing", "can_view_internal_costing", "can_costing_approve"], "note": ""},
        {"title": "Admin / Accounting", "fields": ["can_view_ceo_tools", "can_accounting_bd", "can_accounting_ca"], "note": ""},
    ]

    rows = []
    for u in users:
        access, _ = UserAccess.objects.get_or_create(user=u)
        can_edit = request.user.is_superuser or not u.is_superuser
        if error_form is not None and error_user_id == u.id:
            form = error_form
        else:
            form = UserAccessForm(
                instance=access,
                prefix=f"user_{u.id}",
                can_manage_ceo_tools=request.user.is_superuser,
            )

        if not can_edit:
            for field in form.fields.values():
                field.disabled = True

        grouped_fields = []
        for group in field_groups:
            items = []
            for field_name in group["fields"]:
                if field_name in form.fields:
                    items.append(form[field_name])
            grouped_fields.append({"title": group["title"], "fields": items, "note": group.get("note", "")})

        rows.append(
            {
                "user": u,
                "access": access,
                "form": form,
                "grouped_fields": grouped_fields,
                "can_edit": can_edit,
            }
        )

    return render(
        request,
        "crm/access_list.html",
        {
            "rows": rows,
            "search_query": q,
            "summary": {
                "total_users": User.objects.count(),
                "active_users": User.objects.filter(is_active=True).count(),
                "superusers": User.objects.filter(is_superuser=True).count(),
                "staff_users": User.objects.filter(is_staff=True).count(),
            },
        },
    )


@login_required
@user_passes_test(is_admin_user, login_url="/accounts/login/", redirect_field_name="next")
def access_edit(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    access, _ = UserAccess.objects.get_or_create(user=target_user)

    # Safety: only superuser can edit a superuser access row
    if target_user.is_superuser and not request.user.is_superuser:
        return HttpResponseForbidden("No access")

    if request.method == "POST":
        original_ceo_tools_access = access.can_view_ceo_tools
        before_library_permissions = _library_permission_snapshot(access)
        form = UserAccessForm(
            request.POST,
            instance=access,
            can_manage_ceo_tools=request.user.is_superuser,
        )
        if form.is_valid():
            obj = form.save(commit=False)
            if not request.user.is_superuser:
                obj.can_view_ceo_tools = original_ceo_tools_access

            # Extra safety: BD can never have CA accounting
            if obj.role == UserAccess.ROLE_BD:
                obj.can_accounting_ca = False

            obj.save()
            _write_library_permission_audit(
                request.user,
                target_user,
                before_library_permissions,
                _library_permission_snapshot(obj),
            )
            return redirect("access_list")
    else:
        form = UserAccessForm(instance=access, can_manage_ceo_tools=request.user.is_superuser)

    return render(
        request,
        "crm/access_edit.html",
        {
            "target_user": target_user,
            "access": access,
            "form": form,
        },
    )
