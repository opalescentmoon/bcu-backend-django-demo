import hashlib
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.emails import send_templated_email
from core.exceptions import BusinessError
from core.imaging import open_normalized

from .models import ImageLicenseOption, ImageProduct, ImageStatus
from .watermark import generate_preview_and_thumbnail

logger = logging.getLogger(__name__)


def _checksum(file) -> str:
    digest = hashlib.sha256()
    file.seek(0)
    for chunk in iter(lambda: file.read(1024 * 1024), b""):
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def _watermark_text(vendor) -> str:
    return f"{settings.WATERMARK_TEXT} © {vendor.user.username}"


def _delete_files(*fields):
    for field in fields:
        if field and field.name:
            try:
                field.storage.delete(field.name)
            except Exception:  # noqa: BLE001
                logger.warning("Gagal menghapus file %s", field.name)


def _attach_file(image: ImageProduct, file, meta: dict) -> None:
    """Simpan original (privat) + preview/thumbnail (publik) ke instance."""
    checksum = _checksum(file)
    duplicate = ImageProduct.objects.filter(vendor=image.vendor, checksum_sha256=checksum, is_deleted=False)
    if image.pk:
        duplicate = duplicate.exclude(pk=image.pk)
    if duplicate.exists():
        raise BusinessError("duplicate_file", "File ini sudah pernah Anda unggah.")

    preview, thumb = generate_preview_and_thumbnail(open_normalized(file), _watermark_text(image.vendor))
    image.original_file.save(file.name, file, save=False)
    image.watermarked_preview.save("preview.jpg", preview, save=False)
    image.thumbnail.save("thumb.jpg", thumb, save=False)
    image.width, image.height = meta["width"], meta["height"]
    image.file_format = meta["format"]
    image.file_size = file.size
    image.checksum_sha256 = checksum


def _sync_licenses(image: ImageProduct, licenses: list[dict]) -> None:
    given = {item["license_type"]: item for item in licenses}
    for license_type, item in given.items():
        ImageLicenseOption.objects.update_or_create(
            image=image,
            license_type=license_type,
            defaults={"price": item["price"], "is_active": item.get("is_active", True)},
        )
    image.licenses.exclude(license_type__in=given.keys()).update(is_active=False)
    image.refresh_min_price()


def create_image_product(vendor, *, file, meta, licenses, **fields) -> ImageProduct:
    image = ImageProduct(vendor=vendor, **fields)
    _attach_file(image, file, meta)
    try:
        with transaction.atomic():
            image.save()
            _sync_licenses(image, licenses)
    except Exception:
        _delete_files(image.original_file, image.watermarked_preview, image.thumbnail)  # kompensasi
        raise
    return image


def update_image_product(image: ImageProduct, *, file=None, meta=None, licenses=None, **fields) -> ImageProduct:
    old_files = None
    if file is not None:
        old_files = (image.original_file.name, image.watermarked_preview.name, image.thumbnail.name)
        _attach_file(image, file, meta)
        image.status = ImageStatus.PENDING_REVIEW  # ganti file → review ulang (docs/05 §2)
        image.rejection_reason = ""
    for key, value in fields.items():
        setattr(image, key, value)
    if image.status == ImageStatus.REJECTED and fields:
        image.status = ImageStatus.PENDING_REVIEW
    with transaction.atomic():
        image.save()
        if licenses is not None:
            _sync_licenses(image, licenses)
    if old_files and not image.purchases.exists():
        for storage, name in zip(
            (image.original_file.storage, image.watermarked_preview.storage, image.thumbnail.storage),
            old_files,
            strict=True,
        ):
            storage.delete(name)
    return image


def moderate_image(image: ImageProduct, status: str, *, by=None, reason: str = "") -> None:
    image.status = status
    image.reviewed_by = by
    image.reviewed_at = timezone.now()
    image.rejection_reason = reason if status == ImageStatus.REJECTED else ""
    image.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    logger.info("Moderasi gambar", extra={"image_id": image.pk, "status": status})
    send_templated_email(image.vendor.user.email, "Hasil review gambar", "image_moderation", {"image": image})


def validate_license_prices(licenses: list[dict]) -> None:
    active = {i["license_type"]: Decimal(i["price"]) for i in licenses if i.get("is_active", True)}
    if not active:
        raise ValueError("Minimal satu lisensi aktif.")
    if "personal" in active and "commercial" in active and active["commercial"] < active["personal"]:
        raise ValueError("Harga lisensi Komersial tidak boleh lebih murah dari Personal.")
