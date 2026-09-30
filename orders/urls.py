from django.urls import path

from . import views

urlpatterns = [
    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/items/", views.CartItemCreateView.as_view(), name="cart-items"),
    path("cart/items/<int:pk>/", views.CartItemDetailView.as_view(), name="cart-item-detail"),
    path("orders/checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("orders/history/", views.OrderHistoryView.as_view(), name="order-history"),
    path("orders/<str:order_number>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:order_number>/pay/", views.OrderPayView.as_view(), name="order-pay"),
    path("orders/<str:order_number>/cancel/", views.OrderCancelView.as_view(), name="order-cancel"),
    path(
        "orders/<str:order_number>/vendor-orders/<int:pk>/confirm-received/",
        views.ConfirmReceivedView.as_view(),
        name="order-confirm-received",
    ),
    path("payments/midtrans/notification/", views.MidtransNotificationView.as_view(), name="midtrans-notification"),
    path("payments/simulate/<str:order_number>/", views.SimulatePaymentView.as_view(), name="payment-simulate"),
    path("vendor/dashboard/", views.VendorDashboardView.as_view(), name="vendor-dashboard"),
    path("vendor/orders/", views.VendorOrderListView.as_view(), name="vendor-orders"),
    path("vendor/orders/<int:pk>/", views.VendorOrderDetailView.as_view(), name="vendor-order-detail"),
    path("vendor/orders/<int:pk>/status/", views.VendorOrderStatusView.as_view(), name="vendor-order-status"),
]
