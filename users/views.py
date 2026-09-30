from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import update_last_login
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from core.exceptions import BusinessError
from core.permissions import IsEmailVerified, IsVendor

from . import services
from .models import Address, User, VendorProfile, VendorStatus
from .serializers import (
    AddressSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetSerializer,
    PublicVendorSerializer,
    RefreshSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    TokenSerializer,
    UserSerializer,
    VendorProfileSerializer,
)


def _token_response(user, status_code=status.HTTP_200_OK):
    data = {**services.issue_tokens(user), "user": UserSerializer(user).data}
    return Response(data, status=status_code)


@extend_schema(tags=["auth"], request=RegisterSerializer, responses={201: TokenPairSerializer})
class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        services.send_verification_email(user)
        return _token_response(user, status.HTTP_201_CREATED)


@extend_schema(tags=["auth"], request=LoginSerializer, responses={200: TokenPairSerializer})
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request, email=serializer.validated_data["email"], password=serializer.validated_data["password"]
        )
        if user is None or not user.is_active:
            raise exceptions.AuthenticationFailed("Email atau password salah.", code="invalid_credentials")
        update_last_login(None, user)
        return _token_response(user)


@extend_schema(tags=["auth"], request=RefreshSerializer, responses={200: RefreshSerializer})
class RefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc), code="token_not_valid") from exc
        return Response(serializer.validated_data)


@extend_schema(tags=["auth"], request=RefreshSerializer, responses={205: None})
class LogoutView(APIView):
    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            if str(token.get("user_id")) != str(request.user.pk):
                raise exceptions.PermissionDenied()
            token.blacklist()
        except TokenError:
            pass  # token sudah tidak berlaku → logout tetap dianggap berhasil
        return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(tags=["auth"], request=TokenSerializer, responses={200: dict})
class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not services.verify_email_token(serializer.validated_data["token"]):
            raise exceptions.ValidationError({"token": ["Tautan verifikasi tidak valid atau sudah kedaluwarsa."]})
        return Response({"detail": "Email berhasil diverifikasi."})


@extend_schema(tags=["auth"], request=None, responses={202: dict})
class ResendVerificationView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "resend_verification"

    def post(self, request):
        if request.user.is_email_verified:
            return Response({"detail": "Email sudah terverifikasi."})
        services.send_verification_email(request.user)
        return Response({"detail": "Email verifikasi dikirim."}, status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["auth"], request=PasswordResetSerializer, responses={202: dict})
class PasswordResetView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.send_password_reset_email(serializer.validated_data["email"])
        return Response(
            {"detail": "Jika email terdaftar, tautan reset password telah dikirim."},
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(tags=["auth"], request=PasswordResetConfirmSerializer, responses={200: dict})
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = services.get_user_for_reset(data["uid"], data["token"])
        if not user:
            raise exceptions.ValidationError({"token": ["Tautan reset tidak valid atau sudah kedaluwarsa."]})
        try:
            password_validation.validate_password(data["new_password"], user)
        except Exception as exc:
            raise exceptions.ValidationError({"new_password": list(exc.messages)}) from exc
        user.set_password(data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        services.blacklist_all_tokens(user)
        return Response({"detail": "Password berhasil diubah. Silakan login kembali."})


@extend_schema(tags=["auth"])
class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    http_method_names = ["get", "patch"]

    def get_object(self):
        return User.objects.select_related("vendor_profile").get(pk=self.request.user.pk)


@extend_schema(tags=["auth"], request=ChangePasswordSerializer, responses={200: dict})
class ChangePasswordView(APIView):
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])
        return Response({"detail": "Password berhasil diubah."})


@extend_schema(tags=["alamat"])
class AddressViewSet(viewsets.ModelViewSet):
    queryset = Address.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = AddressSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def perform_destroy(self, instance):
        was_default = instance.is_default
        user = instance.user
        instance.delete()
        if was_default:
            latest = user.addresses.order_by("-created_at").first()
            if latest:
                latest.is_default = True
                latest.save(update_fields=["is_default", "updated_at"])

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        address = self.get_object()
        self.get_queryset().filter(is_default=True).exclude(pk=address.pk).update(is_default=False)
        address.is_default = True
        address.save(update_fields=["is_default", "updated_at"])
        return Response(AddressSerializer(address).data)


@extend_schema(tags=["vendor"])
class MyVendorProfileView(
    mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, generics.GenericAPIView
):
    """GET/POST/PATCH /api/vendor/profile/ — profil toko milik vendor yang login."""

    serializer_class = VendorProfileSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated(), IsVendor()]
        return [IsAuthenticated(), IsVendor(), IsEmailVerified()]

    def get_object(self):
        return get_object_or_404(VendorProfile, user=self.request.user)

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if VendorProfile.objects.filter(user=request.user).exists():
            raise BusinessError("profile_exists", "Profil toko sudah ada. Gunakan PATCH untuk mengubah.")
        return self.create(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)


@extend_schema(tags=["vendor"])
class PublicVendorView(generics.RetrieveAPIView):
    serializer_class = PublicVendorSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        return VendorProfile.objects.filter(status=VendorStatus.APPROVED).annotate(
            food_count=Count("foods", filter=Q(foods__is_available=True, foods__is_deleted=False), distinct=True),
            image_count=Count("images", filter=Q(images__status="published", images__is_deleted=False), distinct=True),
        )
