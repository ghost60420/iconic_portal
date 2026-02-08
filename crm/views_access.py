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

        form = UserAccessForm(request.POST, instance=access, prefix=f"user_{target_user.id}")
        if form.is_valid():
            obj = form.save(commit=False)
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

    rows = []
    for u in users:
        access, _ = UserAccess.objects.get_or_create(user=u)
        can_edit = request.user.is_superuser or not u.is_superuser
        if error_form is not None and error_user_id == u.id:
            form = error_form
        else:
            form = UserAccessForm(instance=access, prefix=f"user_{u.id}")

        if not can_edit:
            for field in form.fields.values():
                field.disabled = True

        rows.append({"user": u, "access": access, "form": form, "can_edit": can_edit})

    field_groups = [
        ("Core", ["can_leads", "can_opportunities", "can_customers", "can_inventory", "can_library"]),
        ("Operations", ["can_production", "can_shipping", "can_accounting_bd", "can_accounting_ca"]),
        ("Engagement", ["can_ai", "can_calendar", "can_marketing", "can_whatsapp"]),
        ("Costing", ["can_costing", "can_costing_approve"]),
    ]

    return render(
        request,
        "crm/access_list.html",
        {
            "rows": rows,
            "field_groups": field_groups,
            "search_query": q,
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
        form = UserAccessForm(request.POST, instance=access)
        if form.is_valid():
            obj = form.save(commit=False)

            # Extra safety: BD can never have CA accounting
            if obj.role == UserAccess.ROLE_BD:
                obj.can_accounting_ca = False

            obj.save()
            return redirect("access_list")
    else:
        form = UserAccessForm(instance=access)

    return render(
        request,
        "crm/access_edit.html",
        {
            "target_user": target_user,
            "access": access,
            "form": form,
        },
    )
