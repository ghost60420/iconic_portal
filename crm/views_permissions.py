from django.contrib.auth.models import AnonymousUser

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect

from .models import AccountingEntry

def can_edit_entry(user, entry) -> bool:
    if not user or isinstance(user, AnonymousUser) or not getattr(user, "is_authenticated", False):
        return False

    # Admin can edit everything
    if getattr(user, "is_superuser", False):
        return True

    # If you have a field like created_by on AccountingEntry, allow owner edit
    if hasattr(entry, "created_by_id") and entry.created_by_id:
        return entry.created_by_id == user.id

    # Fallback: allow staff
    if getattr(user, "is_staff", False):
        return True

    return False


def can_delete_entry(user, entry) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "is_superuser", False)

@login_required
def accounting_entry_delete(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("Delete must be POST.")

    entry = get_object_or_404(AccountingEntry, pk=pk)

    if not request.user.is_superuser:
        return HttpResponseForbidden("Only admin can delete.")

    entry.delete()
    messages.success(request, "Deleted.")
    return redirect("accounting_entry_list")
