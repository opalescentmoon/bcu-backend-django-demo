import logging

from django.conf import settings
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import exceptions, generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.exceptions import BusinessError
from core.permissions import IsApprovedVendor, IsEmailVerified

from . import cart as cart_logic
from . import services
from .dashboard import vendor_dashboard
from .models import CartItem, ItemType, Order, OrderItem, VendorOrder, VendorOrderStatus
from .payments import InvalidSignature, PaymentProviderError, get_provider
from .serializers import (
    CancelSerializer,
    CartItemCreateSerializer,
    CartItemUpdateSerializer,
    CheckoutSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    PaymentSerializer,
    SimulatePaymentSerializer,
    VendorOrderSerializer,
    VendorOrderStatusSerializer,
    cart_payload,
)

logger = logging.getLogger(__name__)

ITEM_PREFETCH = Prefetch("items", queryset=OrderItem.objects.select_related("food", "image").order_by("id"))


def _order_queryset():
    return Order.objects.select_related("user").prefetch_related(
        ITEM_PREFETCH,
        "payments",
        Prefetch(
            "vendor_orders",
            queryset=VendorOrder.objects.select_related("vendor", "order")
            .prefetch_related(ITEM_PREFETCH)
            .order_by("id"),
        ),
    )


def _cart_response(user, status_code=status.HTTP_200_OK):
    cart = cart_logic.get_cart(user)
    return Response(cart_payload(cart_logic.summarize(cart, user)), status=status_code)


# --- Keranjang -------------------------------------------------------------------
@extend_schema(tags=["keranjang"])
class CartView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        return _cart_response(request.user)

    @extend_schema(responses={204: None})
    def delete(self, request):
        cart_logic.get_cart(request.user).items.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["keranjang"], request=CartItemCreateSerializer, responses={201: dict})
class CartItemCreateView(APIView):
    def post(self, request):
        serializer = CartItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["item_type"] == ItemType.FOOD:
            cart_logic.add_food(request.user, data["food_variant"], data["quantity"])
        else:
            cart_logic.add_image(request.user, data["image_license"])
        return _cart_response(request.user, status.HTTP_201_CREATED)


@extend_schema(tags=["keranjang"])
class CartItemDetailView(APIView):
    def _item(self, request, pk):
        return get_object_or_404(
            CartItem.objects.select_related("food_variant__food__vendor"), pk=pk, cart__user=request.user
        )

    @extend_schema(request=CartItemUpdateSerializer, responses={200: dict})
    def patch(self, request, pk):
        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart_logic.update_quantity(self._item(request, pk), serializer.validated_data["quantity"], request.user)
        return _cart_response(request.user)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        self._item(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Checkout & pesanan customer -------------------------------------------------
def _payment_or_none(order):
    try:
        return services.start_payment(order)
    except PaymentProviderError:
        logger.exception("Gagal memulai pembayaran", extra={"order_number": order.order_number})
        return None


@extend_schema(tags=["pesanan"], request=CheckoutSerializer, responses={201: dict})
class CheckoutView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "checkout"

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = services.checkout(request.user, **serializer.validated_data)
        payment = _payment_or_none(order)
        order = _order_queryset().get(pk=order.pk)
        return Response(
            {
                "order": OrderDetailSerializer(order).data,
                "payment": PaymentSerializer(payment).data if payment else None,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["pesanan"],
    parameters=[
        OpenApiParameter("order_type", str, enum=["food", "image"]),
        OpenApiParameter("status", str, enum=["pending", "paid", "completed", "cancelled", "expired"]),
    ],
)
class OrderHistoryView(generics.ListAPIView):
    queryset = Order.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = OrderListSerializer

    def get_queryset(self):
        qs = Order.objects.filter(user=self.request.user).prefetch_related(
            ITEM_PREFETCH, Prefetch("vendor_orders", queryset=VendorOrder.objects.select_related("vendor"))
        )
        params = self.request.query_params
        if params.get("order_type"):
            qs = qs.filter(order_type=params["order_type"])
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        return qs.order_by("-created_at")


class OwnOrderMixin:
    def get_order(self, request, order_number) -> Order:
        order = get_object_or_404(Order, order_number=order_number, user=request.user)
        services.expire_if_overdue(order)
        return order

    def detail_response(self, order):
        return Response(OrderDetailSerializer(_order_queryset().get(pk=order.pk)).data)


@extend_schema(tags=["pesanan"], responses={200: OrderDetailSerializer})
class OrderDetailView(OwnOrderMixin, APIView):
    def get(self, request, order_number):
        return self.detail_response(self.get_order(request, order_number))


@extend_schema(tags=["pesanan"], request=None, responses={200: PaymentSerializer})
class OrderPayView(OwnOrderMixin, APIView):
    """Buat ulang sesi pembayaran untuk order pending."""

    def post(self, request, order_number):
        order = self.get_order(request, order_number)
        if order.status != "pending":
            raise BusinessError("order_not_payable", "Pesanan ini tidak menunggu pembayaran.")
        latest = order.payments.order_by("-created_at").first()
        if latest and latest.status == "pending" and (latest.snap_token or latest.provider == "simulation"):
            payment = latest
        else:
            payment = _payment_or_none(order)
            if payment is None:
                raise exceptions.APIException("Gagal menghubungi payment gateway, coba lagi.")
        return Response(PaymentSerializer(payment).data)


@extend_schema(tags=["pesanan"], request=CancelSerializer, responses={200: OrderDetailSerializer})
class OrderCancelView(OwnOrderMixin, APIView):
    def post(self, request, order_number):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.cancel_order(self.get_order(request, order_number), serializer.validated_data["reason"])
        return self.detail_response(order)


@extend_schema(tags=["pesanan"], request=None, responses={200: OrderDetailSerializer})
class ConfirmReceivedView(OwnOrderMixin, APIView):
    def post(self, request, order_number, pk):
        order = self.get_order(request, order_number)
        vendor_order = get_object_or_404(VendorOrder, pk=pk, order=order)
        services.transition_vendor_order(vendor_order, VendorOrderStatus.COMPLETED, actor="customer")
        return self.detail_response(order)


# --- Pembayaran ------------------------------------------------------------------
@extend_schema(tags=["pembayaran"], request=dict, responses={200: dict})
class MidtransNotificationView(APIView):
    """Webhook Midtrans. Diverifikasi dengan signature_key, bukan JWT."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            notification = get_provider("midtrans").parse_notification(dict(data))
        except InvalidSignature:
            logger.warning("Signature notifikasi Midtrans tidak valid", extra={"order_id": data.get("order_id")})
            raise exceptions.PermissionDenied("Signature tidak valid.") from None
        except (PaymentProviderError, KeyError) as exc:
            logger.exception("Gagal memproses notifikasi Midtrans")
            raise exceptions.APIException("Gagal memproses notifikasi.") from exc
        services.apply_payment_notification(notification)
        return Response({"status": "ok"})


@extend_schema(tags=["pembayaran"], request=SimulatePaymentSerializer, responses={200: OrderDetailSerializer})
class SimulatePaymentView(OwnOrderMixin, APIView):
    """Hanya aktif bila PAYMENT_PROVIDER=simulation (docs/05 §4.3)."""

    def post(self, request, order_number):
        if settings.PAYMENT_PROVIDER != "simulation":
            raise Http404
        serializer = SimulatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = get_object_or_404(Order, order_number=order_number, user=request.user)
        payment = order.payments.filter(provider="simulation").order_by("-created_at").first()
        if payment is None:
            payment = services.start_payment(order)
        notification = get_provider("simulation").parse_notification(
            {
                "provider_order_id": payment.provider_order_id,
                "result": serializer.validated_data["result"],
                "gross_amount": payment.amount,
            }
        )
        services.apply_payment_notification(notification)
        return self.detail_response(order)


# --- Vendor ----------------------------------------------------------------------
@extend_schema(
    tags=["vendor"],
    parameters=[
        OpenApiParameter("status", str, description="Satu atau lebih, dipisah koma: new,processing"),
        OpenApiParameter("order_type", str, enum=["food", "image"]),
    ],
)
class VendorOrderListView(generics.ListAPIView):
    queryset = VendorOrder.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = VendorOrderSerializer
    permission_classes = [IsApprovedVendor]

    def get_queryset(self):
        qs = (
            VendorOrder.objects.filter(vendor=self.request.user.vendor_profile)
            .exclude(status=VendorOrderStatus.AWAITING_PAYMENT)
            .select_related("order__user")
            .prefetch_related(ITEM_PREFETCH)
        )
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("order_type"):
            qs = qs.filter(order__order_type=params["order_type"])
        return qs.order_by("-created_at")


@extend_schema(tags=["vendor"])
class VendorOrderDetailView(generics.RetrieveAPIView):
    queryset = VendorOrder.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = VendorOrderSerializer
    permission_classes = [IsApprovedVendor]

    def get_queryset(self):
        return VendorOrderListView.get_queryset(self)


@extend_schema(tags=["vendor"], request=VendorOrderStatusSerializer, responses={200: VendorOrderSerializer})
class VendorOrderStatusView(APIView):
    permission_classes = [IsApprovedVendor]

    def post(self, request, pk):
        vendor_order = get_object_or_404(VendorOrder, pk=pk, vendor=request.user.vendor_profile)
        serializer = VendorOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.transition_vendor_order(
            vendor_order,
            serializer.validated_data["status"],
            actor="vendor",
            reason=serializer.validated_data["reason"],
        )
        fresh = VendorOrder.objects.select_related("order__user").prefetch_related(ITEM_PREFETCH).get(pk=pk)
        return Response(VendorOrderSerializer(fresh).data)


@extend_schema(
    tags=["vendor"], parameters=[OpenApiParameter("period", str, enum=["7d", "30d", "12m"])], responses={200: dict}
)
class VendorDashboardView(APIView):
    permission_classes = [IsApprovedVendor]

    def get(self, request):
        return Response(vendor_dashboard(request.user.vendor_profile, request.query_params.get("period", "30d")))
