from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from . import services
from .models import Address, User, VendorProfile, VendorStatus


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["-created_at"]
    list_display = ["email", "username", "role", "is_email_verified", "is_active", "created_at"]
    list_filter = ["role", "is_email_verified", "is_active", "is_staff"]
    search_fields = ["email", "username", "first_name", "last_name", "phone"]
    readonly_fields = ["created_at", "updated_at", "last_login"]
    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        ("Profil", {"fields": ("first_name", "last_name", "phone", "role", "is_email_verified")}),
        ("Hak akses", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Waktu", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "username", "role", "password1", "password2")}),)


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ["label", "recipient_name", "user", "city", "is_default"]
    search_fields = ["recipient_name", "user__email", "city"]
    raw_id_fields = ["user"]


@admin.register(VendorProfile)
class VendorProfileAdmin(admin.ModelAdmin):
    list_display = ["store_name", "user", "city", "sells_food", "sells_images", "status", "is_open", "created_at"]
    list_filter = ["status", "sells_food", "sells_images", "city"]
    search_fields = ["store_name", "user__email", "city"]
    readonly_fields = ["slug", "approved_at", "approved_by", "created_at", "updated_at"]
    raw_id_fields = ["user"]
    actions = ["approve", "reject", "suspend"]

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            new_status = obj.status
            obj.status = form.initial.get("status")
            services.set_vendor_status(obj, new_status, by=request.user, reason=obj.rejection_reason)
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="Setujui vendor terpilih")
    def approve(self, request, queryset):
        for vendor in queryset.exclude(status=VendorStatus.APPROVED):
            services.set_vendor_status(vendor, VendorStatus.APPROVED, by=request.user)
        self.message_user(request, "Vendor disetujui.", messages.SUCCESS)

    @admin.action(description="Tolak vendor terpilih (isi alasan penolakan terlebih dahulu)")
    def reject(self, request, queryset):
        missing = queryset.filter(rejection_reason="")
        if missing.exists():
            self.message_user(request, "Isi 'alasan penolakan' pada vendor terlebih dahulu.", messages.ERROR)
            return
        for vendor in queryset:
            services.set_vendor_status(vendor, VendorStatus.REJECTED, by=request.user)
        self.message_user(request, "Vendor ditolak.", messages.WARNING)

    @admin.action(description="Tangguhkan (suspend) vendor terpilih")
    def suspend(self, request, queryset):
        for vendor in queryset.filter(status=VendorStatus.APPROVED):
            services.set_vendor_status(vendor, VendorStatus.SUSPENDED, by=request.user)
        self.message_user(request, "Vendor ditangguhkan.", messages.WARNING)
