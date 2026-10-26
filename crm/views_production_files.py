"""Authenticated delivery for existing media URLs, including production files.

The web server must proxy /media/ here instead of serving MEDIA_ROOT directly.
No copied files, public redirects, signed URLs, or permission caches are used.
"""
import mimetypes
import os
import stat
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseNotFound
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from crm.models import (
    Lead, LeadComment, Opportunity, ProductReferenceImage, ProductionOrder,
    ProductionOrderAttachment, ProductionProgressPhoto,
)
from crm.permissions import require_any_access
from crm.services.operations_permissions import (
    can_access_operations_module, scope_production_orders,
    scope_sales_lead_queue, scope_sales_opportunities,
)
from crm.services.restricted_production import is_restricted_production_user


_FINANCIAL_DIRECTORIES = {"financial_core", "accounting", "accounting_docs"}
_PRODUCTION_DIRECTORIES = {"production_styles", "production_progress", "production_attachments"}
_INLINE_IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}


def _path_parts(path):
    # Reject aliases instead of normalizing a dangerous path into another file.
    if not isinstance(path, str) or "\\" in path or "\x00" in path:
        raise Http404
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Http404
    return parts


def _production_orders_for_file(path):
    # Query the stored relationships, including legacy names outside upload_to.
    return ProductionOrder.objects.filter(
        Q(style_image=path)
        | Q(pk__in=ProductionProgressPhoto.objects.filter(image=path).values("order_id"))
        | Q(pk__in=ProductionOrderAttachment.objects.filter(file=path).values("order_id"))
        | Q(pk__in=ProductReferenceImage.objects.filter(image=path).values("production_order_id"))
        | Q(pk__in=LeadComment.objects.filter(attachment=path).values("production_id"))
    )


def _restricted_can_download(user, path):
    # Attachments have no reviewed manufacturing type: never infer it from a
    # filename, line relationship or an opportunity's unclassified document.
    if Path(path).suffix.lower() not in ProductReferenceImage.ALLOWED_EXTENSIONS:
        return False
    return (
        ProductionOrder.objects.filter(style_image=path, assigned_production_manager=user).exists()
        or ProductionProgressPhoto.objects.filter(
            image=path, order__assigned_production_manager=user,
        ).exists()
        or ProductReferenceImage.objects.filter(
            image=path, production_order__assigned_production_manager=user,
        ).exists()
    )


def _employee_can_download_shared_reference(user, path):
    # Existing reference images can appear on a lead, its opportunities and a
    # production order. Keep an employee's existing source-screen access when
    # that same image is also linked to an order outside their production scope.
    # The restricted account cannot reach this helper.
    if can_access_operations_module(user, "leads") and scope_sales_lead_queue(
        Lead.objects.filter(product_reference_images__image=path), user,
    ).exists():
        return True
    return can_access_operations_module(user, "opportunities") and scope_sales_opportunities(
        Opportunity.objects.filter(
            Q(product_reference_images__image=path) | Q(lead__product_reference_images__image=path)
        ), user,
    ).exists()


def _open_media_file(parts):
    """Open beneath MEDIA_ROOT without following symlinks, including races."""
    directory_fd = None
    file_fd = None
    try:
        root = Path(settings.MEDIA_ROOT).resolve(strict=True)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise Http404
        handle = os.fdopen(file_fd, "rb")
        file_fd = None  # FileResponse owns the handle from this point.
        return handle
    except (OSError, ValueError):
        raise Http404 from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _file_response(parts):
    suffix = Path(parts[-1]).suffix.lower()
    inline_type = _INLINE_IMAGE_TYPES.get(suffix)
    content_type = inline_type or mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
    response = FileResponse(
        _open_media_file(parts), content_type=content_type,
        as_attachment=inline_type is None, filename=parts[-1],
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Referrer-Policy"] = "no-referrer"
    # Do not emit validators: every request must re-check the current assignment.
    return response


@require_any_access("can_production", "can_opportunities", "can_accounting_ca", "can_accounting_bd")
def _production_file_response(request, parts, orders):
    if not scope_production_orders(orders, request.user).exists():
        raise Http404
    return _file_response(parts)


def _protected_media_response(request, path):
    parts = _path_parts(path)
    # Match the existing nginx deny rules; finance retains its dedicated guards.
    if parts[0] in _FINANCIAL_DIRECTORIES:
        raise Http404
    orders = _production_orders_for_file(path)
    production_file = parts[0] in _PRODUCTION_DIRECTORIES or orders.exists()
    if not request.user.is_authenticated:
        if production_file:
            return redirect_to_login(request.get_full_path())
        # Preserve the existing public-media contract. Stored production links
        # take precedence, even for legacy files outside production directories.
        return _file_response(parts)
    if is_restricted_production_user(request.user):
        if not _restricted_can_download(request.user, path):
            raise Http404
        return _file_response(parts)

    if production_file:
        if _employee_can_download_shared_reference(request.user, path):
            return _file_response(parts)
        return _production_file_response(request, parts, orders)
    # Preserve existing employee access to unrelated media; the restricted
    # account never reaches this branch.
    return _file_response(parts)


@never_cache
@require_safe
def protected_media(request, path):
    try:
        return _protected_media_response(request, path)
    except Http404:
        # Keep denials uncacheable too, without disclosing a filesystem path.
        return HttpResponseNotFound("Not found")
