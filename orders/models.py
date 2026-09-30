from django.conf import settings
from django.db import models
from django.db.models import F, Q


class ItemType(models.TextChoices):
    FOOD = "food", "Makanan"
    IMAGE = "image", "Aset Gambar"


class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "keranjang"
        verbose_name_plural = "keranjang"

    def __str__(self):
        return f"Keranjang {self.user}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    item_type = models.CharField(max_length=5, choices=ItemType.choices)
    food_variant = models.ForeignKey(
        "food.FoodVariant", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    image_license = models.ForeignKey(
        "images.ImageLicenseOption", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "item keranjang"
        verbose_name_plural = "item keranjang"
        ordering = ["added_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(item_type="food", food_variant__isnull=False, image_license__isnull=True)
                    | Q(item_type="image", image_license__isnull=False, food_variant__isnull=True, quantity=1)
                ),
                name="cartitem_type_consistent",
            ),
            models.CheckConstraint(condition=Q(quantity__gte=1), name="cartitem_quantity_gte_1"),
            models.UniqueConstraint(fields=["cart", "food_variant"], name="cartitem_unique_variant"),
            models.UniqueConstraint(fields=["cart", "image_license"], name="cartitem_unique_license"),
        ]

    def __str__(self):
        return f"{self.get_item_type_display()} × {self.quantity}"


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Menunggu Pembayaran"
    PAID = "paid", "Dibayar"
    COMPLETED = "completed", "Selesai"
    CANCELLED = "cancelled", "Dibatalkan"
    EXPIRED = "expired", "Kedaluwarsa"


class RefundStatus(models.TextChoices):
    NONE = "none", "Tidak ada"
    PENDING = "pending", "Menunggu refund"
    REFUNDED = "refunded", "Sudah direfund"


class Order(models.Model):
    order_number = models.CharField(max_length=30, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    order_type = models.CharField(max_length=5, choices=ItemType.choices)
    status = models.CharField(max_length=10, choices=OrderStatus.choices, default=OrderStatus.PENDING, db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=30, blank=True)
    shipping_recipient = models.CharField(max_length=120, blank=True)
    shipping_phone = models.CharField(max_length=20, blank=True)
    shipping_address = models.TextField(blank=True)
    shipping_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    shipping_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    refund_status = models.CharField(max_length=10, choices=RefundStatus.choices, default=RefundStatus.NONE)
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pesanan"
        verbose_name_plural = "pesanan"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(order_type="food") | Q(delivery_fee_total=0), name="order_image_no_delivery"
            ),
            models.CheckConstraint(
                condition=Q(total_amount=F("subtotal") + F("delivery_fee_total")), name="order_total_consistent"
            ),
            models.CheckConstraint(condition=Q(status__in=OrderStatus.values), name="order_status_valid"),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="order_user_created_idx"),
            models.Index(
                fields=["status", "expires_at"], name="order_pending_expiry_idx", condition=Q(status="pending")
            ),
        ]

    def __str__(self):
        return self.order_number


class VendorOrderStatus(models.TextChoices):
    AWAITING_PAYMENT = "awaiting_payment", "Menunggu Pembayaran"
    NEW = "new", "Pesanan Baru"
    PROCESSING = "processing", "Diproses"
    SHIPPED = "shipped", "Dikirim"
    COMPLETED = "completed", "Selesai"
    CANCELLED = "cancelled", "Dibatalkan"


class PayoutStatus(models.TextChoices):
    UNPAID = "unpaid", "Belum dicairkan"
    PAID = "paid", "Sudah dicairkan"


class VendorOrder(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="vendor_orders")
    vendor = models.ForeignKey("users.VendorProfile", on_delete=models.PROTECT, related_name="vendor_orders")
    status = models.CharField(
        max_length=16, choices=VendorOrderStatus.choices, default=VendorOrderStatus.AWAITING_PAYMENT, db_index=True
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    vendor_earning = models.DecimalField(max_digits=12, decimal_places=2)
    payout_status = models.CharField(max_length=8, choices=PayoutStatus.choices, default=PayoutStatus.UNPAID)
    paid_out_at = models.DateTimeField(null=True, blank=True)
    processing_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "sub-pesanan vendor"
        verbose_name_plural = "sub-pesanan vendor"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["order", "vendor"], name="vendororder_order_vendor_unique"),
            models.CheckConstraint(condition=Q(status__in=VendorOrderStatus.values), name="vendororder_status_valid"),
        ]
        indexes = [models.Index(fields=["vendor", "status", "-created_at"], name="vendororder_vendor_idx")]

    def __str__(self):
        return f"{self.order.order_number} / {self.vendor}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    vendor_order = models.ForeignKey(VendorOrder, on_delete=models.CASCADE, related_name="items")
    product_type = models.CharField(max_length=5, choices=ItemType.choices)
    food = models.ForeignKey(
        "food.FoodProduct", on_delete=models.PROTECT, null=True, blank=True, related_name="order_items"
    )
    food_variant = models.ForeignKey(
        "food.FoodVariant", on_delete=models.PROTECT, null=True, blank=True, related_name="order_items"
    )
    image = models.ForeignKey(
        "images.ImageProduct", on_delete=models.PROTECT, null=True, blank=True, related_name="order_items"
    )
    license_type = models.CharField(max_length=12, blank=True)
    title_snapshot = models.CharField(max_length=150)
    variant_snapshot = models.CharField(max_length=60, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "item pesanan"
        verbose_name_plural = "item pesanan"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(product_type="food", food__isnull=False, food_variant__isnull=False, image__isnull=True)
                    | (
                        Q(
                            product_type="image",
                            image__isnull=False,
                            food__isnull=True,
                            food_variant__isnull=True,
                            quantity=1,
                        )
                        & ~Q(license_type="")
                    )
                ),
                name="orderitem_type_consistent",
            ),
            models.CheckConstraint(condition=Q(quantity__gte=1), name="orderitem_quantity_gte_1"),
        ]

    def __str__(self):
        return f"{self.title_snapshot} × {self.quantity}"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SETTLEMENT = "settlement", "Lunas"
    EXPIRE = "expire", "Kedaluwarsa"
    CANCEL = "cancel", "Dibatalkan"
    DENY = "deny", "Ditolak"
    FAILURE = "failure", "Gagal"


class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=12)
    provider_order_id = models.CharField(max_length=50, unique=True)
    provider_transaction_id = models.CharField(max_length=64, blank=True)
    snap_token = models.CharField(max_length=64, blank=True)
    redirect_url = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    payment_type = models.CharField(max_length=30, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    raw_notification = models.JSONField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pembayaran"
        verbose_name_plural = "pembayaran"
        ordering = ["-created_at"]

    def __str__(self):
        return self.provider_order_id
