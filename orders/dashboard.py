"""Statistik dashboard vendor (docs/04 §3.9)."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone

from .models import OrderItem, OrderStatus, PayoutStatus, VendorOrder, VendorOrderStatus

PERIODS = {
    "7d": (timedelta(days=7), TruncDate),
    "30d": (timedelta(days=30), TruncDate),
    "12m": (timedelta(days=365), TruncMonth),
}
ZERO = Decimal("0")


def vendor_dashboard(vendor, period: str = "30d") -> dict:
    span, trunc = PERIODS.get(period, PERIODS["30d"])
    since = timezone.now() - span
    counted = VendorOrder.objects.filter(
        vendor=vendor, order__status__in=[OrderStatus.PAID, OrderStatus.COMPLETED]
    ).exclude(status=VendorOrderStatus.CANCELLED)
    in_period = counted.filter(order__paid_at__gte=since)

    totals = in_period.aggregate(
        gross_sales=Coalesce(Sum("subtotal"), ZERO),
        net_earning=Coalesce(Sum("vendor_earning"), ZERO),
        orders_count=Count("id"),
    )
    need_action = VendorOrder.objects.filter(
        vendor=vendor, status__in=[VendorOrderStatus.NEW, VendorOrderStatus.PROCESSING]
    ).count()
    unpaid = counted.filter(status=VendorOrderStatus.COMPLETED, payout_status=PayoutStatus.UNPAID).aggregate(
        total=Coalesce(Sum("vendor_earning"), ZERO)
    )["total"]

    series = (
        in_period.annotate(bucket=trunc("order__paid_at"))
        .values("bucket")
        .annotate(gross_sales=Sum("subtotal"), orders=Count("id"))
        .order_by("bucket")
    )

    top = (
        OrderItem.objects.filter(vendor_order__in=in_period)
        .values("product_type", "food_id", "image_id", "title_snapshot")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:5]
    )

    return {
        "period": period if period in PERIODS else "30d",
        "summary": {**totals, "orders_need_action": need_action, "unpaid_payout": unpaid},
        "series": [
            {
                "date": row["bucket"].date() if hasattr(row["bucket"], "date") else row["bucket"],
                "gross_sales": row["gross_sales"],
                "orders": row["orders"],
            }
            for row in series
        ],
        "top_products": [
            {
                "product_type": row["product_type"],
                "id": row["food_id"] or row["image_id"],
                "title": row["title_snapshot"],
                "quantity": row["quantity"],
                "revenue": row["revenue"],
            }
            for row in top
        ],
    }
