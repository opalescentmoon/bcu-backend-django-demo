from django.contrib import admin

from .models import PlatformSetting


@admin.register(PlatformSetting)
class PlatformSettingAdmin(admin.ModelAdmin):
    list_display = ["__str__", "default_commission_rate", "order_payment_timeout_minutes", "download_url_ttl_minutes"]

    def has_add_permission(self, request):
        return not PlatformSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
