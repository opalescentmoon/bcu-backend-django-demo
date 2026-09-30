from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Min

from core.storage import private_storage, public_storage
from core.utils import UUIDPath, unique_slug


class ImageStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Menunggu Review"
    PUBLISHED = "published", "Terbit"
    REJECTED = "rejected", "Ditolak"


class LicenseType(models.TextChoices):
    PERSONAL = "personal", "Personal"
    COMMERCIAL = "commercial", "Komersial"


LICENSE_RANK = {LicenseType.PERSONAL: 1, LicenseType.COMMERCIAL: 2}


class ImageProductQuerySet(models.QuerySet):
    def public(self):
        return self.filter(status=ImageStatus.PUBLISHED, is_deleted=False, vendor__status="approved")


class ImageProduct(models.Model):
    vendor = models.ForeignKey("users.VendorProfile", on_delete=models.CASCADE, related_name="images")
    category = models.ForeignKey("catalog.Category", on_delete=models.PROTECT, related_name="images")
    title = models.CharField("judul", max_length=150)
    slug = models.SlugField(max_length=170)
    description = models.TextField("deskripsi", blank=True)
    tags = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    # File original TIDAK PERNAH diserialisasi ke API (docs/07 §2)
    original_file = models.FileField(upload_to=UUIDPath("originals"), storage=private_storage, max_length=255)
    watermarked_preview = models.ImageField(upload_to=UUIDPath("previews"), storage=public_storage, max_length=255)
    thumbnail = models.ImageField(upload_to=UUIDPath("thumbs"), storage=public_storage, max_length=255)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    file_size = models.BigIntegerField()
    file_format = models.CharField(max_length=10)
    checksum_sha256 = models.CharField(max_length=64)
    status = models.CharField(
        max_length=15, choices=ImageStatus.choices, default=ImageStatus.PENDING_REVIEW, db_index=True
    )
    rejection_reason = models.TextField("alasan penolakan", blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    min_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_deleted = models.BooleanField(default=False)
    search_vector = SearchVectorField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ImageProductQuerySet.as_manager()

    class Meta:
        verbose_name = "aset gambar"
        verbose_name_plural = "aset gambar"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["vendor", "slug"], name="image_vendor_slug_unique"),
            models.CheckConstraint(condition=models.Q(status__in=ImageStatus.values), name="image_status_valid"),
        ]
        indexes = [
            models.Index(fields=["status", "is_deleted", "category", "min_price"], name="image_gallery_idx"),
            GinIndex(fields=["search_vector"], name="image_search_idx"),
            GinIndex(fields=["tags"], name="image_tags_idx"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(ImageProduct, self.title, max_length=170, vendor=self.vendor)
        super().save(*args, **kwargs)
        ImageProduct.objects.filter(pk=self.pk).update(
            search_vector=SearchVector("title", weight="A", config="simple")
            + SearchVector("description", weight="B", config="simple")
        )

    def refresh_min_price(self):
        self.min_price = self.licenses.filter(is_active=True).aggregate(m=Min("price"))["m"] or 0
        ImageProduct.objects.filter(pk=self.pk).update(min_price=self.min_price)

    @property
    def orientation(self) -> str:
        if self.width == self.height:
            return "square"
        return "landscape" if self.width > self.height else "portrait"

    @property
    def download_filename(self):
        ext = "jpg" if self.file_format == "jpeg" else self.file_format
        return f"{self.slug}.{ext}"


class ImageLicenseOption(models.Model):
    image = models.ForeignKey(ImageProduct, on_delete=models.CASCADE, related_name="licenses")
    license_type = models.CharField(max_length=12, choices=LicenseType.choices)
    price = models.DecimalField("harga", max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "opsi lisensi"
        verbose_name_plural = "opsi lisensi"
        ordering = ["price"]
        constraints = [
            models.UniqueConstraint(fields=["image", "license_type"], name="license_image_type_unique"),
            models.CheckConstraint(condition=models.Q(price__gt=0), name="license_price_gt_0"),
            models.CheckConstraint(condition=models.Q(license_type__in=LicenseType.values), name="license_type_valid"),
        ]

    def __str__(self):
        return f"{self.image.title} — {self.get_license_type_display()}"


class ImagePurchase(models.Model):
    """Hak unduh; dibuat hanya oleh handler pembayaran settlement."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="image_purchases")
    image = models.ForeignKey(ImageProduct, on_delete=models.PROTECT, related_name="purchases")
    license_type = models.CharField(max_length=12, choices=LicenseType.choices)
    order_item = models.OneToOneField("orders.OrderItem", on_delete=models.PROTECT, related_name="image_purchase")
    download_count = models.PositiveIntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)
    purchased_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "hak unduh"
        verbose_name_plural = "hak unduh"
        ordering = ["-purchased_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "image", "license_type"], name="purchase_user_image_license_unique")
        ]

    def __str__(self):
        return f"{self.user} — {self.image} ({self.license_type})"


class DownloadLog(models.Model):
    purchase = models.ForeignKey(ImagePurchase, on_delete=models.CASCADE, related_name="logs")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    url_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "log unduhan"
        verbose_name_plural = "log unduhan"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.purchase} @ {self.created_at:%Y-%m-%d %H:%M}"
