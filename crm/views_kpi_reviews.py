import calendar
import time
from dataclasses import dataclass
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from crm.forms_kpi_reviews import (
    KPIReviewCreateForm,
    KPIReviewDecisionForm,
    KPIReviewEntryForm,
    KPIReviewRejectForm,
)
from crm.models_employee import EmployeeProfile
from crm.models_kpi_reviews import (
    KPIReview,
    KPIReviewItemEntry,
    KPIReviewTransition,
)
from crm.services.employee_profiles import can_manage_employees
from crm.services.kpi_assignments import assignments_for_date
from crm.services.kpi_review_permissions import (
    can_approve_kpi_review,
    can_create_kpi_review,
    can_lock_kpi_review,
    can_manage_kpi_review,
    can_manage_kpi_review_queue,
    can_open_kpi_review_queue,
    can_view_employee_performance,
    can_view_kpi_review,
    visible_kpi_employees,
    visible_kpi_reviews,
)
from crm.services.kpi_reviews import (
    KPIReviewWorkflowError,
    approve_kpi_review,
    create_kpi_review,
    lock_kpi_review,
    reject_kpi_review,
    save_kpi_review_draft,
    start_kpi_review,
    submit_kpi_review,
    verify_approved_snapshot,
)


@dataclass(frozen=True)
class WindowPage:
    object_list: tuple
    number: int
    page_size: int
    has_next: bool

    @property
    def has_previous(self):
        return self.number > 1

    def previous_page_number(self):
        return self.number - 1

    def next_page_number(self):
        return self.number + 1

    def __iter__(self):
        return iter(self.object_list)


def _window_page(queryset, raw_page, *, page_size):
    try:
        page_number = max(int(raw_page or 1), 1)
    except (TypeError, ValueError):
        page_number = 1
    offset = (page_number - 1) * page_size
    rows = list(queryset[offset : offset + page_size + 1])
    return WindowPage(
        object_list=tuple(rows[:page_size]),
        number=page_number,
        page_size=page_size,
        has_next=len(rows) > page_size,
    )


def _review_queryset():
    return KPIReview.objects.select_related(
        "employee__user",
        "employee__department_ref",
        "manager__employee_profile",
        "manager__access",
        "submitted_by__employee_profile",
        "submitted_by__access",
        "review_started_by__employee_profile",
        "review_started_by__access",
        "approved_by__employee_profile",
        "approved_by__access",
        "rejected_by__employee_profile",
        "rejected_by__access",
        "locked_by__employee_profile",
        "locked_by__access",
    ).prefetch_related(
        Prefetch(
            "item_entries",
            queryset=KPIReviewItemEntry.objects.select_related(
                "assignment__kpi_template",
                "kpi_item__template_version",
            ),
        ),
        Prefetch(
            "transitions",
            queryset=KPIReviewTransition.objects.select_related(
                "actor__employee_profile"
            ),
        ),
    )


def _visible_review_or_404(user, pk):
    review = get_object_or_404(_review_queryset(), pk=pk)
    for field in (
        "manager",
        "submitted_by",
        "review_started_by",
        "approved_by",
        "rejected_by",
        "locked_by",
    ):
        related_user = getattr(review, field, None)
        if related_user and related_user.pk == user.pk:
            for cache_key in ("employee_profile", "access"):
                cached = related_user._state.fields_cache.get(cache_key)
                if cached is not None:
                    user._state.fields_cache[cache_key] = cached
                    if cache_key == "access":
                        user._crm_user_access_cached = cached
            break
    if not can_view_kpi_review(user, review):
        return None
    return review


def _current_month_defaults():
    today = timezone.localdate()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return {
        "period_type": KPIReview.PERIOD_MONTHLY,
        "period_start": date(today.year, today.month, 1),
        "period_end": date(today.year, today.month, last_day),
        "review_date": today,
    }


def _snapshot_entry_rows(review, entries):
    definition_items = {}
    for role in review.definition_snapshot.get("roles", []):
        for item in role.get("items", []):
            definition_items[
                (role["assignment_id"], item["kpi_item_id"])
            ] = (role, item)

    approved_entries = {
        (entry["assignment_id"], entry["kpi_item_id"]): entry
        for entry in review.approved_snapshot.get("entries", [])
    }
    rows = []
    for entry in entries:
        key = (entry.assignment_id, entry.kpi_item_id)
        role, item = definition_items[key]
        values = approved_entries.get(key)
        rows.append(
            {
                "role_name": role["template_name"],
                "kpi_name": item["name"],
                "actual_value": (
                    values["actual_value"] if values else entry.actual_value
                ),
                "critical_red": (
                    values["critical_red"] if values else entry.critical_red
                ),
                "critical_reason": (
                    values["critical_reason"] if values else entry.critical_reason
                ),
                "critical_trigger": (
                    values["critical_trigger"] if values else entry.critical_trigger
                ),
                "item_comment": (
                    values["item_comment"] if values else entry.item_comment
                ),
            }
        )
    return rows


@login_required
def employee_performance(request, user_id):
    started = time.perf_counter()
    employee = get_object_or_404(
        EmployeeProfile.objects.select_related(
            "user",
            "user__access",
            "manager__employee_profile",
            "department_ref",
            "position_ref",
        ),
        user_id=user_id,
    )
    if not can_view_employee_performance(request.user, employee):
        return HttpResponseForbidden("You cannot view this employee's KPI performance.")
    if employee.user_id == request.user.pk:
        request.user._state.fields_cache["access"] = employee.user.access
        request.user._crm_user_access_cached = employee.user.access

    today = timezone.localdate()
    current_assignments = list(assignments_for_date(employee, today))
    completed = (
        KPIReview.objects.select_related(
            "manager__employee_profile",
            "approved_by__employee_profile",
        )
        .filter(
            employee=employee,
            status__in=(KPIReview.STATUS_APPROVED, KPIReview.STATUS_LOCKED),
        )
        .order_by("-period_end", "-approved_at")
    )
    history_page = _window_page(completed, request.GET.get("page"), page_size=20)
    latest_completed = (
        history_page.object_list[0]
        if history_page.number == 1 and history_page.object_list
        else completed.first()
    )
    latest_result = latest_completed.result_snapshot if latest_completed else {}
    latest_roles = latest_result.get("roles", [])
    manager_ids = {
        assignment.manager_id
        for assignment in current_assignments
        if assignment.manager_id
    }
    assigned_manager = (
        current_assignments[0].manager
        if len(manager_ids) == 1 and current_assignments
        else None
    )
    latest_review = (
        KPIReview.objects.filter(employee=employee)
        .only("status", "period_type", "period_start", "period_end")
        .order_by("-period_end", "-id")
        .first()
        if request.user.pk != employee.user_id
        else latest_completed
    )
    response = render(
        request,
        "crm/kpi/employee_performance.html",
        {
            "employee": employee,
            "current_assignments": current_assignments,
            "assigned_manager": assigned_manager,
            "latest_review": latest_review,
            "latest_completed": latest_completed,
            "latest_result": latest_result,
            "latest_roles": latest_roles,
            "history_page": history_page,
            "can_open_profile": can_manage_employees(request.user),
            "can_open_review_queue": (
                request.user.pk != employee.user_id
                and can_open_kpi_review_queue(request.user)
            ),
        },
    )
    response["Server-Timing"] = (
        f"kpi-performance;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
def kpi_review_list(request):
    started = time.perf_counter()
    if not can_open_kpi_review_queue(request.user):
        return HttpResponseForbidden("KPI review management is restricted.")
    employees = visible_kpi_employees(request.user).order_by(
        "display_name", "user__username"
    )
    status_filter = (request.GET.get("status") or "").strip()
    reviews = visible_kpi_reviews(request.user)
    if status_filter in dict(KPIReview.STATUS_CHOICES):
        reviews = reviews.filter(status=status_filter)
    reviews_page = Paginator(reviews, 25).get_page(request.GET.get("page"))

    form = KPIReviewCreateForm(
        request.POST or None,
        employee_queryset=employees,
        initial=_current_month_defaults(),
    )
    if request.method == "POST" and form.is_valid():
        employee = form.cleaned_data["employee"]
        if not can_create_kpi_review(
            request.user,
            employee,
            review_date=form.cleaned_data["review_date"],
        ):
            return HttpResponseForbidden("You cannot create this employee review.")
        try:
            review = create_kpi_review(
                employee=employee,
                period_type=form.cleaned_data["period_type"],
                period_start=form.cleaned_data["period_start"],
                period_end=form.cleaned_data["period_end"],
                review_date=form.cleaned_data["review_date"],
                actor=request.user,
            )
        except KPIReviewWorkflowError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "KPI review opened in Draft.")
            return redirect("kpi_review_detail", pk=review.pk)

    response = render(
        request,
        "crm/kpi/review_list.html",
        {
            "create_form": form,
            "reviews_page": reviews_page,
            "status_filter": status_filter,
            "status_choices": KPIReview.STATUS_CHOICES,
            "show_create_form": can_manage_kpi_review_queue(request.user),
        },
    )
    response["Server-Timing"] = (
        f"kpi-review-list;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
def kpi_review_detail(request, pk):
    started = time.perf_counter()
    review = _visible_review_or_404(request.user, pk)
    if review is None:
        return HttpResponseForbidden("You cannot view this KPI review.")
    entries = list(review.item_entries.all())
    can_manage = can_manage_kpi_review(request.user, review)
    can_approve = can_approve_kpi_review(request.user, review)
    entry_form = (
        KPIReviewEntryForm(review=review, entries=entries)
        if review.status == KPIReview.STATUS_DRAFT and can_manage
        else None
    )
    result = review.result_snapshot
    response = render(
        request,
        "crm/kpi/review_detail.html",
        {
            "review": review,
            "entries": entries,
            "display_entries": _snapshot_entry_rows(review, entries),
            "entry_form": entry_form,
            "result": result,
            "role_results": result.get("roles", []),
            "can_manage_review": can_manage,
            "can_approve_review": can_approve,
            "can_lock_review": can_lock_kpi_review(request.user, review),
            "decision_form": KPIReviewDecisionForm(),
            "reject_form": KPIReviewRejectForm(),
            "snapshot_valid": (
                verify_approved_snapshot(review)
                if review.approved_snapshot
                else None
            ),
        },
    )
    response["Server-Timing"] = (
        f"kpi-review-detail;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
@require_POST
def kpi_review_save(request, pk):
    review = _visible_review_or_404(request.user, pk)
    if review is None or not can_manage_kpi_review(request.user, review):
        return HttpResponseForbidden("You cannot edit this KPI review.")
    entries = list(review.item_entries.all())
    form = KPIReviewEntryForm(request.POST, review=review, entries=entries)
    if not form.is_valid():
        result = review.result_snapshot
        return render(
            request,
            "crm/kpi/review_detail.html",
            {
                "review": review,
                "entries": entries,
                "display_entries": _snapshot_entry_rows(review, entries),
                "entry_form": form,
                "result": result,
                "role_results": result.get("roles", []),
                "can_manage_review": True,
                "can_approve_review": can_approve_kpi_review(
                    request.user, review
                ),
                "can_lock_review": can_lock_kpi_review(request.user, review),
                "decision_form": KPIReviewDecisionForm(),
                "reject_form": KPIReviewRejectForm(),
                "snapshot_valid": None,
            },
            status=400,
        )
    try:
        review = save_kpi_review_draft(
            review,
            actor=request.user,
            entry_values=form.entry_values(),
            manager_comment=form.cleaned_data["manager_comment"],
        )
        if request.POST.get("intent") == "submit":
            review = submit_kpi_review(review, actor=request.user)
    except KPIReviewWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "KPI review submitted."
            if review.status == KPIReview.STATUS_SUBMITTED
            else "KPI review draft saved.",
        )
    return redirect("kpi_review_detail", pk=review.pk)


@login_required
@require_POST
def kpi_review_submit(request, pk):
    review = _visible_review_or_404(request.user, pk)
    if review is None or not can_manage_kpi_review(request.user, review):
        return HttpResponseForbidden("You cannot submit this KPI review.")
    try:
        submit_kpi_review(review, actor=request.user)
    except KPIReviewWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "KPI review submitted.")
    return redirect("kpi_review_detail", pk=pk)


@login_required
@require_POST
def kpi_review_start(request, pk):
    review = _visible_review_or_404(request.user, pk)
    if review is None or not can_approve_kpi_review(request.user, review):
        return HttpResponseForbidden("You cannot review this KPI submission.")
    try:
        start_kpi_review(review, actor=request.user)
    except KPIReviewWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "KPI review moved Under Review.")
    return redirect("kpi_review_detail", pk=pk)


@login_required
@require_POST
def kpi_review_approve(request, pk):
    review = _visible_review_or_404(request.user, pk)
    if review is None or not can_approve_kpi_review(request.user, review):
        return HttpResponseForbidden("You cannot approve this KPI review.")
    form = KPIReviewDecisionForm(request.POST)
    if form.is_valid():
        try:
            approve_kpi_review(
                review,
                actor=request.user,
                comment=form.cleaned_data["comment"],
            )
        except KPIReviewWorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "KPI review approved and made read only.")
    return redirect("kpi_review_detail", pk=pk)


@login_required
@require_POST
def kpi_review_reject(request, pk):
    review = _visible_review_or_404(request.user, pk)
    if review is None or not can_approve_kpi_review(request.user, review):
        return HttpResponseForbidden("You cannot reject this KPI review.")
    form = KPIReviewRejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A rejection comment is required.")
        return redirect("kpi_review_detail", pk=pk)
    try:
        reject_kpi_review(
            review,
            actor=request.user,
            comment=form.cleaned_data["comment"],
        )
    except KPIReviewWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "KPI review rejected and returned to Draft.")
    return redirect("kpi_review_detail", pk=pk)


@login_required
@require_POST
def kpi_review_lock(request, pk):
    review = _visible_review_or_404(request.user, pk)
    if review is None or not can_lock_kpi_review(request.user, review):
        return HttpResponseForbidden("You cannot lock this KPI review.")
    try:
        lock_kpi_review(review, actor=request.user)
    except KPIReviewWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "KPI review locked.")
    return redirect("kpi_review_detail", pk=pk)
