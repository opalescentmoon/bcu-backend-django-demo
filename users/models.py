from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models.functions import Lower

from core.storage import public_storage
from core.utils import UUIDPath, unique_slug


class Role(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    VENDOR = "vendor", "Vendor"
    ADMIN = "admin", "Admin"


class EmailUserManager(UserManager):
    def _create_user(self, username, email, password, **extra_fields):
        email = self.normalize_email(email).lower()
        return super()._create_user(username, email, password, **extra_fields)

    def create_user(self, email, password=None, **extra_fields):
        username = extra_fields.pop("username", None) or email.split("@")[0]
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        username = extra_fields.pop("username", None) or email.split("@")[0]
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_email_verified", True)
        return super().create_superuser(username, email, password, **extra_fields)

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)


class User(AbstractUser):
    email = models.EmailField("email", max_length=254, unique=True)
    phone = models.CharField("no. HP", max_length=20, blank=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CUSTOMER)
    is_email_verified = models.BooleanField("email terverifikasi", default=False)
    date_joined = None
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    objects = EmailUserManager()

    class Meta:
        verbose_name = "pengguna"
        verbose_name_plural = "pengguna"
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
            models.CheckConstraint(condition=models.Q(role__in=Role.values), name="user_role_valid"),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        if self.is_superuser and self.role != Role.ADMIN:
            self.role = Role.ADMIN
        super().save(*args, **kwargs)

    @property
    def vendor(self):
        return getattr(self, "vendor_profile", None)


class Address(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=50)
    recipient_name = models.CharField("nama penerima", max_length=120)
    phone = models.CharField(max_length=20)
    street = models.TextField("alamat")
    city = models.CharField("kota", max_length=100)
    province = models.CharField("provinsi", max_length=100)
    postal_code = models.CharField("kode pos", max_length=10)
    notes = models.CharField("catatan", max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "alamat"
        verbose_name_plural = "alamat"
        ordering = ["-is_default", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(is_default=True), name="address_one_default_per_user"
            )
        ]

    def __str__(self):
        return f"{self.label} — {self.recipient_name}"

    @property
    def full_address(self):
        return f"{self.street}, {self.city}, {self.province} {self.postal_code}"


class VendorStatus(models.TextChoices):
    PENDING = "pending", "Menunggu"
    APPROVED = "approved", "Disetujui"
    REJECTED = "rejected", "Ditolak"
    SUSPENDED = "suspended", "Ditangguhkan"


class VendorProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vendor_profile")
    store_name = models.CharField("nama toko", max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField("deskripsi", blank=True)
    phone = models.CharField(max_length=20)
    address = models.TextField("alamat toko")
    city = models.CharField("kota", max_length=100, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    logo = models.ImageField(upload_to=UUIDPath("logos"), storage=public_storage, max_length=255, blank=True)
    sells_food = models.BooleanField("menjual makanan", default=False)
    sells_images = models.BooleanField("menjual aset gambar", default=False)
    delivery_fee = models.DecimalField("ongkir tetap", max_digits=12, decimal_places=2, default=0)
    is_open = models.BooleanField("toko buka", default=True)
    status = models.CharField(max_length=10, choices=VendorStatus.choices, default=VendorStatus.PENDING, db_index=True)
    rejection_reason = models.TextField("alasan penolakan", blank=True)
    commission_rate = models.DecimalField(
        "override komisi",
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Kosongkan untuk memakai komisi default platform. 0.1000 = 10%.",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profil vendor"
        verbose_name_plural = "profil vendor"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sells_food=True) | models.Q(sells_images=True), name="vendor_sells_something"
            ),
            models.CheckConstraint(condition=models.Q(delivery_fee__gte=0), name="vendor_delivery_fee_gte_0"),
            models.CheckConstraint(condition=models.Q(status__in=VendorStatus.values), name="vendor_status_valid"),
        ]

    def __str__(self):
        return self.store_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(VendorProfile, self.store_name, max_length=140)
        super().save(*args, **kwargs)

    @property
    def is_approved(self):
        return self.status == VendorStatus.APPROVED

    @property
    def effective_commission_rate(self):
        if self.commission_rate is not None:
            return self.commission_rate
        from core.models import PlatformSetting

        return PlatformSetting.load().default_commission_rate
