# crm/views_access.py

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from .forms_access import UserAccessForm
from .models_access import UserAccess

User = get_user_model()


def is_admin_user(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)


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
        ("Core", ["can_leads", "can_opportunities", "can_customers", "can_calendar"]),
        ("Operations", ["can_inventory", "can_library", "can_production", "can_shipping"]),
        ("Engagement", ["can_ai", "can_marketing", "can_whatsapp"]),
        ("Costing", ["can_costing", "can_view_internal_costing", "can_costing_approve"]),
        ("Admin / Accounting", ["can_view_ceo_tools", "can_accounting_bd", "can_accounting_ca"]),
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
        for group_label, field_names in field_groups:
            items = []
            for field_name in field_names:
                if field_name in form.fields:
                    items.append(form[field_name])
            grouped_fields.append((group_label, items))

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
