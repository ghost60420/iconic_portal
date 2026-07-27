import io
import os

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps


CATALOG_IMAGE_FIELDS = (
    (1, "image"),
    (2, "image_2"),
    (3, "image_3"),
)
MAX_CATALOG_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_CATALOG_IMAGE_DIMENSION = 1800
CATALOG_IMAGE_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CATALOG_IMAGE_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def validate_catalog_image_file(image, slot):
    if not image:
        return

    extension = os.path.splitext(image.name or "")[1].lower()
    if extension not in CATALOG_IMAGE_ALLOWED_EXTENSIONS:
        raise ValidationError(f"Image {slot}: upload a JPG, PNG, or WEBP image.")

    content_type = (getattr(image, "content_type", "") or "").lower()
    if content_type and content_type not in CATALOG_IMAGE_ALLOWED_CONTENT_TYPES:
        raise ValidationError(f"Image {slot}: upload a JPG, PNG, or WEBP image.")

    size = getattr(image, "size", 0) or 0
    if size > MAX_CATALOG_IMAGE_UPLOAD_BYTES:
        limit_mb = MAX_CATALOG_IMAGE_UPLOAD_BYTES // (1024 * 1024)
        raise ValidationError(f"Image {slot}: file size must be {limit_mb}MB or smaller.")

    try:
        image.seek(0)
        with Image.open(image) as img:
            img.verify()
    except Exception as exc:
        raise ValidationError(f"Image {slot}: upload a valid image file.") from exc
    finally:
        try:
            image.seek(0)
        except Exception:
            pass


def prepare_catalog_image(image, slot):
    validate_catalog_image_file(image, slot)
    if not image:
        return image

    extension = os.path.splitext(image.name or "")[1].lower()
    output_format = "JPEG"
    output_extension = ".jpg"
    if extension == ".png":
        output_format = "PNG"
        output_extension = ".png"
    elif extension == ".webp":
        output_format = "WEBP"
        output_extension = ".webp"

    image.seek(0)
    with Image.open(image) as img:
        img = ImageOps.exif_transpose(img)
        if max(img.size) > MAX_CATALOG_IMAGE_DIMENSION:
            img.thumbnail(
                (MAX_CATALOG_IMAGE_DIMENSION, MAX_CATALOG_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )

        if output_format in {"JPEG", "WEBP"} and img.mode not in {"RGB", "L"}:
            img = img.convert("RGB")

        buffer = io.BytesIO()
        save_kwargs = {"optimize": True}
        if output_format in {"JPEG", "WEBP"}:
            save_kwargs["quality"] = 82
        img.save(buffer, format=output_format, **save_kwargs)

    base_name = os.path.splitext(os.path.basename(image.name or f"catalog_{slot}"))[0]
    safe_base = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in base_name).strip("_")
    safe_base = safe_base or f"catalog_{slot}"
    return ContentFile(buffer.getvalue(), name=f"{safe_base}{output_extension}")


def catalog_image_slots(obj):
    slots = []
    for slot, field_name in CATALOG_IMAGE_FIELDS:
        image = getattr(obj, field_name, None)
        slots.append(
            {
                "slot": slot,
                "field_name": field_name,
                "image": image,
                "url": image.url if image else "",
                "has_image": bool(image),
            }
        )
    return slots


def catalog_image_count(obj):
    return sum(1 for _slot, field_name in CATALOG_IMAGE_FIELDS if getattr(obj, field_name, None))


def catalog_cover_url(obj):
    for _slot, field_name in CATALOG_IMAGE_FIELDS:
        image = getattr(obj, field_name, None)
        if image:
            return image.url
    return ""


def catalog_image_names(obj):
    return {field_name: (getattr(obj, field_name, None).name if getattr(obj, field_name, None) else "") for _, field_name in CATALOG_IMAGE_FIELDS}
