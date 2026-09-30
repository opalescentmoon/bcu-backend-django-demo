from django.contrib import admin, messages
from django.utils import timezone

from .models import Order, OrderItem, Payment, PayoutStatus, RefundStatus, VendorOrder


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = [
        "product_type",
        "title_snapshot",
        "variant_snapshot",
        "license_type",
        "unit_price",
        "quantity",
        "line_total",
        "vendor_order",
    ]
    readonly_fields = fields


class VendorOrderInline(admin.TabularInline):
    model = VendorOrder
    extra = 0
    can_delete = False
    fields = ["vendor", "status", "subtotal", "delivery_fee", "commission_amount", "vendor_earning", "payout_status"]
    readonly_fields = ["vendor", "subtotal", "delivery_fee", "commission_amount", "vendor_earning"]
    show_change_link = True


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    fields = ["provider", "provider_order_id", "status", "payment_type", "amount", "settled_at"]
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "user", "order_type", "status", "refund_status", "total_amount", "created_at"]
    list_filter = ["order_type", "status", "refund_status"]
    search_fields = ["order_number", "user__email"]
    date_hierarchy = "created_at"
    readonly_fields = [
        "order_number",
        "user",
        "order_type",
        "subtotal",
        "delivery_fee_total",
        "total_amount",
        "payment_method",
        "shipping_recipient",
        "shipping_phone",
        "shipping_address",
        "expires_at",
        "paid_at",
        "completed_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    ]
    inlines = [VendorOrderInline, OrderItemInline, PaymentInline]
    actions = ["mark_refunded"]

    @admin.action(description="Tandai refund sudah diproses")
    def mark_refunded(self, request, queryset):
        updated = queryset.filter(refund_status=RefundStatus.PENDING).update(refund_status=RefundStatus.REFUNDED)
        self.message_user(request, f"{updated} pesanan ditandai sudah direfund.", messages.SUCCESS)


@admin.register(VendorOrder)
class VendorOrderAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "vendor",
        "status",
        "subtotal",
        "commission_amount",
        "vendor_earning",
        "payout_status",
        "created_at",
    ]
    list_filter = ["status", "payout_status", "vendor"]
    search_fields = ["order__order_number", "vendor__store_name"]
    readonly_fields = [
        "order",
        "vendor",
        "subtotal",
        "delivery_fee",
        "commission_rate",
        "commission_amount",
        "vendor_earning",
        "created_at",
    ]
    actions = ["mark_paid_out"]

    @admin.action(description="Tandai pendapatan sudah dicairkan (payout)")
    def mark_paid_out(self, request, queryset):
        updated = queryset.filter(status="completed", payout_status=PayoutStatus.UNPAID).update(
            payout_status=PayoutStatus.PAID, paid_out_at=timezone.now()
        )
        self.message_user(request, f"{updated} sub-pesanan ditandai sudah dicairkan.", messages.SUCCESS)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["provider_order_id", "order", "provider", "status", "payment_type", "amount", "created_at"]
    list_filter = ["provider", "status"]
    search_fields = ["provider_order_id", "order__order_number"]
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False
