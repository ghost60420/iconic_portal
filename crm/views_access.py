from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import get_user_model
from django.shortcuts import render, redirect, get_object_or_404

from .models_access import UserAccess
from .forms_access import UserAccessForm

User = get_user_model()

def is_admin_user(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)

@login_required
@user_passes_test(is_admin_user)
def access_list(request):
    users = User.objects.all().order_by("username")
    return render(request, "crm/access_list.html", {"users": users})

@login_required
@user_passes_test(is_admin_user)
def access_edit(request, user_id):
    u = get_object_or_404(User, id=user_id)
    access, _ = UserAccess.objects.get_or_create(user=u)

    if request.method == "POST":
        form = UserAccessForm(request.POST, instance=access)
        if form.is_valid():
            form.save()
            return redirect("access_list")
    else:
        form = UserAccessForm(instance=access)

    return render(request, "crm/access_edit.html", {"target_user": u, "form": form})