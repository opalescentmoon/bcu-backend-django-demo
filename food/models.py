from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Min

from core.storage import public_storage
from core.utils import UUIDPath, unique_slug


class FoodProductQuerySet(models.QuerySet):
    def public(self):
        """Produk yang boleh tampil di katalog publik (docs/01 FOOD-01)."""
        return self.filter(is_available=True, is_deleted=False, vendor__status="approved")


class FoodProduct(models.Model):
    vendor = models.ForeignKey("users.VendorProfile", on_delete=models.CASCADE, related_name="foods")
    category = models.ForeignKey("catalog.Category", on_delete=models.PROTECT, related_name="foods")
    title = models.CharField("judul", max_length=150)
    slug = models.SlugField(max_length=170)
    description = models.TextField("deskripsi", blank=True)
    image = models.ImageField(upload_to=UUIDPath("foods"), storage=public_storage, max_length=255)
    is_available = models.BooleanField("tersedia", default=True)
    is_deleted = models.BooleanField(default=False)
    min_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    search_vector = SearchVectorField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FoodProductQuerySet.as_manager()

    class Meta:
        verbose_name = "produk makanan"
        verbose_name_plural = "produk makanan"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["vendor", "slug"], name="food_vendor_slug_unique")]
        indexes = [
            models.Index(fields=["is_available", "is_deleted", "category", "min_price"], name="food_catalog_idx"),
            GinIndex(fields=["search_vector"], name="food_search_idx"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(FoodProduct, self.title, max_length=170, vendor=self.vendor)
        super().save(*args, **kwargs)
        FoodProduct.objects.filter(pk=self.pk).update(
            search_vector=SearchVector("title", weight="A", config="simple")
            + SearchVector("description", weight="B", config="simple")
        )

    def refresh_min_price(self):
        self.min_price = self.variants.filter(is_active=True).aggregate(m=Min("price"))["m"] or 0
        FoodProduct.objects.filter(pk=self.pk).update(min_price=self.min_price)


class FoodVariant(models.Model):
    food = models.ForeignKey(FoodProduct, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField("nama varian", max_length=60)
    price = models.DecimalField("harga", max_digits=12, decimal_places=2)
    stock = models.IntegerField("stok", default=0)
    is_active = models.BooleanField(default=True)
    sort_order = models.SmallIntegerField(default=0)

    class Meta:
        verbose_name = "varian makanan"
        verbose_name_plural = "varian makanan"
        ordering = ["sort_order", "price", "id"]
        constraints = [
            models.UniqueConstraint(fields=["food", "name"], name="variant_food_name_unique"),
            models.CheckConstraint(condition=models.Q(price__gt=0), name="variant_price_gt_0"),
            models.CheckConstraint(condition=models.Q(stock__gte=0), name="variant_stock_gte_0"),
        ]

    def __str__(self):
        return f"{self.food.title} — {self.name}"
