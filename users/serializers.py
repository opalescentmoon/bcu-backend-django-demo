from django.contrib.auth import password_validation
from django.db import transaction
from rest_framework import serializers

from core.validators import validate_image_file

from .models import Address, Role, User, VendorProfile, VendorStatus


class VendorBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorProfile
        fields = ["store_name", "slug", "status", "sells_food", "sells_images"]


class UserSerializer(serializers.ModelSerializer):
    vendor = VendorBriefSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "is_email_verified",
            "vendor",
            "created_at",
        ]
        read_only_fields = ["id", "email", "role", "is_email_verified", "vendor", "created_at"]

    def validate_username(self, value):
        qs = User.objects.filter(username__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Nama pengguna sudah dipakai.")
        return value


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})
    role = serializers.ChoiceField(choices=[Role.CUSTOMER, Role.VENDOR], default=Role.CUSTOMER)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email sudah terdaftar.")
        return value

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Nama pengguna sudah dipakai.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": ["Konfirmasi password tidak sama."]})
        candidate = User(username=attrs["username"], email=attrs["email"])
        try:
            password_validation.validate_password(attrs["password"], candidate)
        except Exception as exc:  # django ValidationError
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Password lama salah.")
        return value

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context["request"].user)
        return value


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "recipient_name",
            "phone",
            "street",
            "city",
            "province",
            "postal_code",
            "notes",
            "latitude",
            "longitude",
            "is_default",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        is_first = not user.addresses.exists()
        make_default = validated_data.pop("is_default", False) or is_first
        if make_default:
            user.addresses.filter(is_default=True).update(is_default=False)
        return Address.objects.create(user=user, is_default=make_default, **validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        if validated_data.get("is_default"):
            instance.user.addresses.exclude(pk=instance.pk).filter(is_default=True).update(is_default=False)
        elif "is_default" in validated_data and instance.is_default:
            validated_data.pop("is_default")  # alamat default tidak bisa di-unset langsung
        return super().update(instance, validated_data)


class VendorProfileSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = VendorProfile
        fields = [
            "store_name",
            "slug",
            "description",
            "phone",
            "address",
            "city",
            "latitude",
            "longitude",
            "logo",
            "logo_url",
            "sells_food",
            "sells_images",
            "delivery_fee",
            "is_open",
            "status",
            "rejection_reason",
            "commission_rate",
            "approved_at",
            "created_at",
        ]
        read_only_fields = ["slug", "status", "rejection_reason", "commission_rate", "approved_at", "created_at"]
        extra_kwargs = {"logo": {"write_only": True, "required": False}}

    def get_logo_url(self, obj) -> str | None:
        return obj.logo.url if obj.logo else None

    def validate_logo(self, value):
        if value:
            validate_image_file(value, max_mb=5, max_side=4000)
        return value

    def validate_delivery_fee(self, value):
        if value < 0:
            raise serializers.ValidationError("Ongkir tidak boleh negatif.")
        return value

    def validate(self, attrs):
        sells_food = attrs.get("sells_food", getattr(self.instance, "sells_food", False))
        sells_images = attrs.get("sells_images", getattr(self.instance, "sells_images", False))
        if not (sells_food or sells_images):
            raise serializers.ValidationError({"sells_food": ["Pilih minimal satu jenis jualan."]})
        return attrs

    def create(self, validated_data):
        return VendorProfile.objects.create(user=self.context["request"].user, **validated_data)

    def update(self, instance, validated_data):
        if instance.status == VendorStatus.REJECTED:
            instance.status = VendorStatus.PENDING  # ajukan ulang setelah perbaikan
        return super().update(instance, validated_data)


class PublicVendorSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()
    food_count = serializers.IntegerField(read_only=True)
    image_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = VendorProfile
        fields = [
            "store_name",
            "slug",
            "description",
            "logo_url",
            "city",
            "is_open",
            "delivery_fee",
            "sells_food",
            "sells_images",
            "food_count",
            "image_count",
        ]

    def get_logo_url(self, obj) -> str | None:
        return obj.logo.url if obj.logo else None
