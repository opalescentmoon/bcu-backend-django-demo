from django.db import models


class CategoryType(models.TextChoices):
    FOOD = "food", "Makanan"
    IMAGE = "image", "Gambar"


class Category(models.Model):
    type = models.CharField(max_length=10, choices=CategoryType.choices)
    name = models.CharField("nama", max_length=80)
    slug = models.SlugField(max_length=100)
    icon = models.CharField(max_length=50, blank=True)
    sort_order = models.SmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "kategori"
        verbose_name_plural = "kategori"
        ordering = ["type", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["type", "slug"], name="category_type_slug_unique"),
            models.CheckConstraint(condition=models.Q(type__in=CategoryType.values), name="category_type_valid"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"
