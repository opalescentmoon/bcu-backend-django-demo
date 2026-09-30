from django.contrib import admin

from .models import FoodProduct, FoodVariant


class FoodVariantInline(admin.TabularInline):
    model = FoodVariant
    extra = 0


@admin.register(FoodProduct)
class FoodProductAdmin(admin.ModelAdmin):
    list_display = ["title", "vendor", "category", "min_price", "is_available", "is_deleted", "created_at"]
    list_filter = ["is_available", "is_deleted", "category"]
    search_fields = ["title", "vendor__store_name"]
    readonly_fields = ["slug", "min_price", "created_at", "updated_at"]
    raw_id_fields = ["vendor"]
    inlines = [FoodVariantInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.refresh_min_price()
