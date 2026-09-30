from django.contrib import admin, messages
from django.utils.html import format_html

from core.storage import private_download_url

from . import services
from .models import DownloadLog, ImageLicenseOption, ImageProduct, ImagePurchase, ImageStatus


class LicenseInline(admin.TabularInline):
    model = ImageLicenseOption
    extra = 0


@admin.register(ImageProduct)
class ImageProductAdmin(admin.ModelAdmin):
    list_display = ["thumb", "title", "vendor", "category", "status", "min_price", "is_deleted", "created_at"]
    list_display_links = ["thumb", "title"]
    list_filter = ["status", "is_deleted", "category"]
    search_fields = ["title", "vendor__store_name", "tags"]
    readonly_fields = [
        "preview",
        "original_link",
        "width",
        "height",
        "file_size",
        "file_format",
        "checksum_sha256",
        "min_price",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    ]
    exclude = ["original_file", "watermarked_preview", "thumbnail"]
    raw_id_fields = ["vendor"]
    inlines = [LicenseInline]
    actions = ["publish", "reject"]

    @admin.display(description="")
    def thumb(self, obj):
        return (
            format_html('<img src="{}" style="height:48px;border-radius:4px">', obj.thumbnail.url)
            if obj.thumbnail
            else ""
        )

    @admin.display(description="Preview (watermark)")
    def preview(self, obj):
        return format_html('<img src="{}" style="max-width:600px">', obj.watermarked_preview.url) if obj.pk else ""

    @admin.display(description="File original (untuk moderasi)")
    def original_link(self, obj):
        if not obj.pk:
            return ""
        url = private_download_url(obj.original_file.name, obj.download_filename, 900)
        return format_html('<a href="{}" target="_blank">Unduh original (berlaku 15 menit)</a>', url)

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            services.moderate_image(obj, obj.status, by=request.user, reason=obj.rejection_reason)
        else:
            super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.refresh_min_price()

    @admin.action(description="Terbitkan gambar terpilih")
    def publish(self, request, queryset):
        for image in queryset.exclude(status=ImageStatus.PUBLISHED):
            services.moderate_image(image, ImageStatus.PUBLISHED, by=request.user)
        self.message_user(request, "Gambar diterbitkan.", messages.SUCCESS)

    @admin.action(description="Tolak gambar terpilih (isi alasan penolakan terlebih dahulu)")
    def reject(self, request, queryset):
        if queryset.filter(rejection_reason="").exists():
            self.message_user(request, "Isi 'alasan penolakan' pada gambar terlebih dahulu.", messages.ERROR)
            return
        for image in queryset:
            services.moderate_image(image, ImageStatus.REJECTED, by=request.user, reason=image.rejection_reason)
        self.message_user(request, "Gambar ditolak.", messages.WARNING)


@admin.register(ImagePurchase)
class ImagePurchaseAdmin(admin.ModelAdmin):
    list_display = ["user", "image", "license_type", "download_count", "purchased_at"]
    list_filter = ["license_type"]
    search_fields = ["user__email", "image__title"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DownloadLog)
class DownloadLogAdmin(admin.ModelAdmin):
    list_display = ["purchase", "ip_address", "created_at", "url_expires_at"]
    search_fields = ["purchase__user__email", "ip_address"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
