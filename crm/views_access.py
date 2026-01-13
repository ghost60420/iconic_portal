# crm/views_access.py

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
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
    # Ensure every user has a UserAccess row
    users = User.objects.all().order_by("username")
    for u in users:
        UserAccess.objects.get_or_create(user=u)

    users = User.objects.select_related("access").all().order_by("username")
    return render(request, "crm/access_list.html", {"users": users})


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