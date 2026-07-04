import time

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from crm.forms_employee import EmployeeProfileForm
from crm.models import EmployeeProfile
from crm.services.chatter_mentions import mention_suggestions
from crm.services.employee_profiles import (
    build_employee_timeline,
    can_archive_employees,
    can_manage_employees,
    can_manage_roles,
    can_view_sales_profile,
    can_view_team_performance,
    employee_audit,
    employee_display_name,
    group_names,
    set_employee_roles,
)
from crm.services.employee_identity import employee_profile_ids_matching
from crm.services.operations_permissions import (
    ROLE_ADMIN,
    ROLE_CEO,
    ROLE_SALES,
    has_operations_role,
    operations_group_names,
)
from crm.services.sales_attribution import (
    build_employee_sales_statistics,
    build_sales_kpis,
    build_team_sales_kpis,
)


def _profile_snapshot(profile):
    user = profile.user
    return {
        "full_name": user.get_full_name(),
        "display_name": profile.display_name,
        "aliases": ", ".join(profile.aliases or []),
        "email": user.email,
        "phone": profile.phone,
        "employee_id": profile.employee_id or "",
        "position": profile.position_name,
        "department": profile.department_name,
        "status": profile.get_status_display(),
        "manager": employee_display_name(profile.manager) if profile.manager else "",
        "active": "Active" if user.is_active else "Inactive",
    }


def _audit_snapshot_changes(actor, target_user, before, after):
    for field_name in before:
        employee_audit(actor, target_user, field_name, before[field_name], after[field_name])


def _apply_profile_form(profile, form):
    draft = form.save(commit=False)
    for field_name in (
        "display_name", "aliases", "phone", "position_ref", "department_ref", "status",
        "manager", "profile_photo", "notes",
    ):
        setattr(profile, field_name, getattr(draft, field_name))
    profile.save()
    cache.delete(f"crm-employee-display:{profile.user_id}")
    return profile


def _selected_roles(request, form, target_user=None):
    selected = list(form.cleaned_data.get("roles") or [])
    if can_manage_roles(request.user):
        if not (request.user.is_superuser or has_operations_role(request.user, ROLE_CEO)):
            ceo = Group.objects.filter(name=ROLE_CEO).first()
            if ceo:
                selected = [role for role in selected if role.pk != ceo.pk]
                if target_user and target_user.groups.filter(pk=ceo.pk).exists():
                    selected.append(ceo)
        return selected
    return list(target_user.groups.all()) if target_user else []


@login_required
def employee_list(request):
    if not can_manage_employees(request.user):
        return HttpResponseForbidden("Employee profiles are restricted to authorized management users.")
    query = (request.GET.get("q") or "").strip()[:100]
    status_filter = (request.GET.get("status") or "").strip()
    archive_filter = (request.GET.get("archive") or "active").strip().lower()
    can_archive = can_archive_employees(request.user)
    if archive_filter not in {"active", "archived", "all"}:
        archive_filter = "active"
    if archive_filter != "active" and not can_archive:
        return HttpResponseForbidden("Archived employee profiles are restricted to CEO and Admin users.")
    sort = (request.GET.get("sort") or "name").strip()
    direction = "desc" if request.GET.get("direction") == "desc" else "asc"
    profiles = EmployeeProfile.objects.select_related(
        "user", "manager", "manager__employee_profile", "position_ref", "department_ref"
    ).prefetch_related("user__groups")
    if archive_filter == "archived":
        profiles = profiles.filter(is_archived=True)
    elif archive_filter != "all":
        profiles = profiles.filter(is_archived=False)
    if query:
        query_key = query.casefold()
        position_values = [
            value for value, label in EmployeeProfile.POSITION_CHOICES
            if query_key in label.casefold() or query_key in value.replace("_", " ").casefold()
        ]
        department_values = [
            value for value, label in EmployeeProfile.DEPARTMENT_CHOICES
            if query_key in label.casefold() or query_key in value.replace("_", " ").casefold()
        ]
        status_values = [
            value for value, label in EmployeeProfile.STATUS_CHOICES
            if query_key == label.casefold() or query_key == value.replace("_", " ").casefold()
        ]
        search_filter = (
            Q(display_name__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(employee_id__icontains=query)
        )
        alias_profile_ids = employee_profile_ids_matching(query)
        if alias_profile_ids:
            search_filter |= Q(pk__in=alias_profile_ids)
        search_filter |= Q(position_ref__name__icontains=query) | Q(department_ref__name__icontains=query)
        if position_values:
            search_filter |= Q(position__in=position_values)
        if department_values:
            search_filter |= Q(department__in=department_values)
        if status_values:
            search_filter |= Q(status__in=status_values)
        profiles = profiles.filter(search_filter)
    valid_statuses = {value for value, _label in EmployeeProfile.STATUS_CHOICES}
    if status_filter in valid_statuses:
        profiles = profiles.filter(status=status_filter)

    sort_fields = {
        "employee_id": "employee_id",
        "department": "department_ref__name",
        "position": "position_ref__name",
        "last_login": "user__last_login",
        "date_joined": "user__date_joined",
    }
    if sort == "name":
        ordering = Lower("display_name").desc() if direction == "desc" else Lower("display_name").asc()
        profiles = profiles.order_by(ordering, "user__username")
    else:
        sort = sort if sort in sort_fields else "name"
        prefix = "-" if direction == "desc" else ""
        profiles = profiles.order_by(f"{prefix}{sort_fields.get(sort, 'display_name')}", "display_name", "user__username")
    page = Paginator(profiles, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/people/employee_list.html",
        {
            "profiles": page.object_list,
            "page_obj": page,
            "query": query,
            "status_filter": status_filter,
            "archive_filter": archive_filter,
            "sort": sort,
            "direction": direction,
            "status_choices": EmployeeProfile.STATUS_CHOICES,
            "can_manage_roles": can_manage_roles(request.user),
            "can_archive_employees": can_archive,
            "can_view_team_performance": can_view_team_performance(request.user),
        },
    )


@login_required
def employee_create(request):
    if not can_manage_employees(request.user):
        return HttpResponseForbidden("Employee profiles are restricted to authorized management users.")
    form = EmployeeProfileForm(request.POST or None, request.FILES or None)
    if not can_manage_roles(request.user):
        form.fields["roles"].disabled = True
        form.fields["is_active"].disabled = True
    if request.method == "POST" and form.is_valid():
        User = get_user_model()
        with transaction.atomic():
            user = User(username=form.cleaned_data["username"])
            user.set_unusable_password()
            user.save()
            form.save_user_fields(user)
            profile = _apply_profile_form(user.employee_profile, form)
            selected = _selected_roles(request, form, user)
            if selected:
                set_employee_roles(actor=request.user, target_user=user, selected_roles=selected)
            employee_audit(request.user, user, "profile", "", f"Created {profile.public_name}")
        messages.success(request, f"Employee profile created for {profile.public_name}.")
        return redirect("employee_edit", user_id=user.pk)
    return render(request, "crm/people/employee_form.html", {"form": form, "creating": True})


@login_required
def employee_edit(request, user_id):
    if not can_manage_employees(request.user):
        return HttpResponseForbidden("Employee profiles are restricted to authorized management users.")
    profile = get_object_or_404(
        EmployeeProfile.objects.select_related("user", "manager", "manager__employee_profile").prefetch_related("user__groups"),
        user_id=user_id,
    )
    target_user = profile.user
    if target_user.pk == request.user.pk:
        target_user._operations_group_names = operations_group_names(request.user)
    if target_user.is_superuser and not request.user.is_superuser:
        return HttpResponseForbidden("Only a superuser can edit another superuser account.")
    original_snapshot = _profile_snapshot(profile)
    form = EmployeeProfileForm(
        request.POST or None,
        request.FILES or None,
        instance=profile,
        user_instance=target_user,
    )
    can_roles = can_manage_roles(request.user)
    if not can_roles:
        form.fields["roles"].disabled = True
        form.fields["is_active"].disabled = True
    if target_user.is_superuser and not request.user.is_superuser:
        form.fields["is_active"].disabled = True
        form.fields["roles"].disabled = True
        can_roles = False

    if request.method == "POST" and form.is_valid():
        before = original_snapshot
        before_roles = group_names(target_user)
        requested_active = form.requested_user_active()
        if profile.is_archived and requested_active:
            form.add_error("is_active", "Restore this employee before enabling CRM login.")
        elif target_user == request.user and not requested_active:
            form.add_error("is_active", "You cannot deactivate your own account.")
        elif (
            target_user.is_active
            and not requested_active
            and target_user.groups.filter(name=ROLE_CEO).exists()
            and Group.objects.get(name=ROLE_CEO).user_set.filter(is_active=True).count() <= 1
        ):
            form.add_error("is_active", "The last active CEO account cannot be deactivated.")
        else:
            with transaction.atomic():
                form.save_user_fields(target_user)
                profile = _apply_profile_form(profile, form)
                if can_roles:
                    selected = _selected_roles(request, form, target_user)
                    if ROLE_CEO in before_roles and ROLE_CEO not in [role.name for role in selected]:
                        if Group.objects.get(name=ROLE_CEO).user_set.filter(is_active=True).count() <= 1:
                            selected.append(Group.objects.get(name=ROLE_CEO))
                            messages.warning(request, "The last active CEO role was preserved.")
                    set_employee_roles(actor=request.user, target_user=target_user, selected_roles=selected)
                after = _profile_snapshot(profile)
                _audit_snapshot_changes(request.user, target_user, before, after)
            messages.success(request, f"Employee profile updated for {profile.public_name}.")
            return redirect("employee_edit", user_id=target_user.pk)

    organization = list(
        EmployeeProfile.objects.select_related("user", "manager")
        .order_by("display_name", "user__username")
    )
    organization_by_user = {item.user_id: item for item in organization}
    management_chain = []
    cursor = profile
    visited = set()
    while cursor and cursor.user_id not in visited:
        visited.add(cursor.user_id)
        management_chain.append(cursor)
        cursor = organization_by_user.get(cursor.manager_id)
    management_chain.reverse()
    direct_reports = [item for item in organization if item.manager_id == target_user.pk]
    employee_statistics = build_employee_sales_statistics(target_user)
    return render(
        request,
        "crm/people/employee_form.html",
        {
            "form": form,
            "profile": profile,
            "target_user": target_user,
            "creating": False,
            "can_manage_roles": can_roles,
            "target_is_sales": has_operations_role(target_user, ROLE_SALES),
            "employee_timeline": build_employee_timeline(profile),
            "management_chain": management_chain,
            "direct_reports": direct_reports,
            "employee_statistics": employee_statistics,
            "can_archive_employees": can_archive_employees(request.user),
        },
    )


def _employee_access_action_error(actor, target_user):
    if target_user.pk == actor.pk:
        return "You cannot deactivate or archive your own account."
    if target_user.is_superuser and not actor.is_superuser:
        return "Only a superuser can deactivate or archive another superuser account."
    if (
        target_user.is_active
        and target_user.groups.filter(name=ROLE_CEO).exists()
        and Group.objects.filter(name=ROLE_CEO, user__is_active=True).count() <= 1
    ):
        return "The last active CEO account cannot be deactivated or archived."
    return ""


@login_required
@require_POST
def employee_deactivate(request, user_id):
    if not can_archive_employees(request.user):
        return HttpResponseForbidden("Only CEO and Admin users can deactivate employees.")
    profile = get_object_or_404(EmployeeProfile.objects.select_related("user"), user_id=user_id)
    target_user = profile.user
    error = _employee_access_action_error(request.user, target_user)
    if error:
        messages.error(request, error)
        return redirect("employee_edit", user_id=target_user.pk)
    if profile.is_archived:
        messages.error(request, "Restore this employee before changing active access.")
        return redirect("employee_edit", user_id=target_user.pk)
    with transaction.atomic():
        was_active = target_user.is_active
        previous_status = profile.get_status_display()
        target_user.is_active = False
        target_user.save(update_fields=["is_active"])
        profile.status = EmployeeProfile.STATUS_INACTIVE
        profile.save(update_fields=["status", "updated_at"])
        if was_active:
            employee_audit(request.user, target_user, "active", "Active", "Inactive")
        employee_audit(request.user, target_user, "status", previous_status, "Inactive")
    messages.success(request, f"{profile.public_name} has been deactivated and can no longer sign in.")
    return redirect("employee_edit", user_id=target_user.pk)


@login_required
@require_POST
def employee_archive(request, user_id):
    if not can_archive_employees(request.user):
        return HttpResponseForbidden("Only CEO and Admin users can archive employees.")
    profile = get_object_or_404(EmployeeProfile.objects.select_related("user"), user_id=user_id)
    target_user = profile.user
    error = _employee_access_action_error(request.user, target_user)
    if error:
        messages.error(request, error)
        return redirect("employee_edit", user_id=target_user.pk)
    if profile.is_archived:
        messages.info(request, f"{profile.public_name} is already archived.")
        return redirect("employee_edit", user_id=target_user.pk)
    with transaction.atomic():
        was_active = target_user.is_active
        previous_status = profile.get_status_display()
        target_user.is_active = False
        target_user.save(update_fields=["is_active"])
        profile.status = EmployeeProfile.STATUS_INACTIVE
        profile.is_archived = True
        profile.archived_at = timezone.now()
        profile.archived_by = request.user
        profile.save(update_fields=["status", "is_archived", "archived_at", "archived_by", "updated_at"])
        if was_active:
            employee_audit(request.user, target_user, "active", "Active", "Inactive")
        employee_audit(request.user, target_user, "status", previous_status, "Inactive")
        employee_audit(request.user, target_user, "archived", "Active directory", "Archived")
    messages.success(request, f"{profile.public_name} has been archived. Historical records were preserved.")
    return redirect("employee_list")


@login_required
@require_POST
def employee_restore(request, user_id):
    if not can_archive_employees(request.user):
        return HttpResponseForbidden("Only CEO and Admin users can restore employees.")
    profile = get_object_or_404(EmployeeProfile.objects.select_related("user"), user_id=user_id)
    if not profile.is_archived:
        messages.info(request, f"{profile.public_name} is not archived.")
        return redirect("employee_edit", user_id=profile.user_id)
    with transaction.atomic():
        profile.is_archived = False
        profile.archived_at = None
        profile.archived_by = None
        profile.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
        employee_audit(request.user, profile.user, "archived", "Archived", "Active directory")
    messages.success(
        request,
        f"{profile.public_name} has been restored to the employee directory. CRM login remains disabled until reactivated.",
    )
    return redirect("employee_edit", user_id=profile.user_id)


@login_required
def mention_suggestions_view(request):
    return JsonResponse({"results": mention_suggestions(request.GET.get("q"))})


@login_required
def salesperson_profile(request, user_id=None):
    started = time.perf_counter()
    target_user = request.user if user_id is None else get_object_or_404(
        get_user_model().objects.select_related("employee_profile").prefetch_related("groups"),
        pk=user_id,
    )
    viewing_self = target_user.pk == request.user.pk
    if not can_view_sales_profile(request.user, target_user):
        return HttpResponseForbidden("You can only view your own sales profile.")
    if not has_operations_role(target_user, ROLE_SALES):
        return HttpResponseForbidden("This employee is not assigned to the Sales role.")
    context = build_sales_kpis(target_user)
    context.update({"salesperson": target_user, "viewing_self": viewing_self})
    response = render(request, "crm/people/salesperson_profile.html", context)
    response["Server-Timing"] = f"sales-profile;dur={(time.perf_counter() - started) * 1000:.1f}"
    return response


@login_required
def team_performance(request):
    started = time.perf_counter()
    if not can_view_team_performance(request.user):
        return HttpResponseForbidden("Team Performance is restricted to CEO and management users.")
    context = build_team_sales_kpis()
    response = render(request, "crm/people/team_performance.html", context)
    response["Server-Timing"] = f"team-performance;dur={(time.perf_counter() - started) * 1000:.1f}"
    return response
