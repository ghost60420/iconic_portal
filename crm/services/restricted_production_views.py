"""Assigned-only projections for the existing production list and detail screens.

These isolated branches are necessary because the employee views assemble linked
sales, cost, inventory and shipping contexts before rendering their templates.
No additional page, route, permission model or workflow is introduced.
"""
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q, Value
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from crm.models import LeadComment, ProductReferenceImage, ProductionOrder, ProductionProgressPhoto, ProductionStage


ORDER_FIELDS = (
    "id", "order_code", "style_name", "color_info", "size_group", "size_ratio_note",
    "accessories_note", "packaging_note", "qty_total", "sample_deadline", "bulk_deadline", "style_image",
)


class RestrictedProgressPhotoForm(forms.Form):
    progress_stage = forms.ChoiceField(choices=ProductionProgressPhoto.STAGE_CHOICES)
    progress_caption = forms.CharField(max_length=160, required=False)
    progress_photo = forms.ImageField()

    def clean_progress_photo(self):
        image = self.cleaned_data["progress_photo"]
        if image.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Choose an image no larger than 10 MB.")
        if image.image.format not in {"JPEG", "PNG", "WEBP"}:
            raise forms.ValidationError("Upload a JPG, PNG, or WEBP image.")
        return image


def restricted_production_list(request):
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD"])
    query = (request.GET.get("q") or "").strip()[:200]
    orders = ProductionOrder.objects.filter(assigned_production_manager_id=request.user.pk)
    if query:
        orders = orders.filter(Q(order_code__icontains=query) | Q(style_name__icontains=query))
    page = Paginator(orders.order_by("-created_at", "-pk").values(*ORDER_FIELDS), 25).get_page(request.GET.get("page"))
    return render(request, "crm/production_list.html", {
        "restricted_production_access": True, "production_page": page, "search_query": query,
    })


@transaction.atomic
def _restricted_detail_update(request, pk):
    # Recheck assignment while holding the order lock. Assignment changes use
    # the same existing order row, so a stale open page cannot grant write access.
    order = get_object_or_404(
        ProductionOrder.objects.select_for_update().filter(assigned_production_manager_id=request.user.pk), pk=pk,
    )
    action = request.POST.get("action")
    if action == "add_comment":
        if request.FILES:
            return HttpResponseForbidden("Use Production Photos for image uploads.")
        note = (request.POST.get("comment_text") or "").strip()
        if not note or len(note) > 10000:
            messages.error(request, "Enter a progress note of 1 to 10,000 characters.")
        else:
            LeadComment.objects.create(
                production=order, author_user=request.user,
                author=(request.user.get_full_name() or request.user.get_username())[:100], content=note,
            )
            messages.success(request, "Production progress note added.")
    elif action == "add_progress_photo":
        form = RestrictedProgressPhotoForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "; ".join(str(error) for errors in form.errors.values() for error in errors))
        else:
            try:
                ProductionProgressPhoto.objects.create(
                    order=order, stage=form.cleaned_data["progress_stage"],
                    image=form.cleaned_data["progress_photo"], caption=form.cleaned_data["progress_caption"],
                    uploaded_by=request.user,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, "Production progress photo added.")
    else:
        return HttpResponseForbidden("This production action is not permitted.")
    return redirect("production_detail", pk=pk)


def restricted_production_detail(request, pk):
    if request.method == "POST":
        return _restricted_detail_update(request, pk)
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD", "POST"])
    order = get_object_or_404(
        ProductionOrder.objects.filter(assigned_production_manager_id=request.user.pk).values(*ORDER_FIELDS), pk=pk,
    )
    order["pk"] = order["id"]
    if order["style_image"]:
        order["style_image_url"] = ProductionOrder._meta.get_field("style_image").storage.url(order["style_image"])
    stages = list(ProductionStage.objects.filter(order_id=pk).exclude(stage_key="shipping"))
    stage_order = {key: index for index, (key, _label) in enumerate(ProductionStage.STAGE_CHOICES)}
    stages.sort(key=lambda stage: stage_order.get(stage.stage_key, 99))
    for stage in stages:
        stage.edit_version = stage.updated_at.isoformat()
    lines = ProductionOrder._meta.get_field("lines").related_model.objects.filter(order_id=pk).values(
        "line_no", "style_name", "color_info", "quantity", "size_ratio_note", "accessories_note", "packaging_note",
    )
    # Historical chatter can contain prices or copied sales conversations. Only
    # this account's production-only progress notes belong in this projection.
    notes = LeadComment.objects.filter(
        production_id=pk, lead__isnull=True, opportunity__isnull=True, author_user=request.user,
    ).order_by("-created_at", "-pk").values("content", "created_at")[:100]
    # Both existing image models share one bounded query. Only references linked
    # explicitly to this production order are eligible, never inherited sales files.
    photos = ProductionProgressPhoto.objects.filter(order_id=pk).order_by().values(
        "image", "caption", "uploaded_at", label=F("stage"),
    )
    references = ProductReferenceImage.objects.filter(production_order_id=pk).order_by().values(
        "image", "caption", "uploaded_at", label=Value("reference"),
    )
    images = list(photos.union(references, all=True).order_by("-uploaded_at")[:100])
    labels = dict(ProductionProgressPhoto.STAGE_CHOICES)
    storage = ProductionProgressPhoto._meta.get_field("image").storage
    for image in images:
        image["url"] = storage.url(image["image"])
        image["label"] = labels.get(image["label"], "Reference image")
    return render(request, "crm/production_detail.html", {
        "restricted_production_access": True, "order": order, "stages": stages,
        "percent_done": int(100 * sum(stage.status == "done" for stage in stages) / len(stages)) if stages else 0,
        "order_delayed": any(stage.status == "delay" or stage.is_late for stage in stages),
        "can_edit_production": True, "stage_status_choices": ProductionStage.STATUS_CHOICES,
        "order_lines": lines, "progress_notes": notes, "progress_photos": images,
        "progress_photo_stage_choices": ProductionProgressPhoto.STAGE_CHOICES,
    })
