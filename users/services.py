import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.emails import send_templated_email

from .models import User, VendorProfile, VendorStatus

logger = logging.getLogger(__name__)
EMAIL_VERIFY_SALT = "email-verify"


# --- Token JWT -------------------------------------------------------------------
def issue_tokens(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    vendor = user.vendor
    refresh["vendor_status"] = vendor.status if vendor else None
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def blacklist_all_tokens(user: User) -> None:
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


# --- Verifikasi email ------------------------------------------------------------
def make_email_verification_token(user: User) -> str:
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=EMAIL_VERIFY_SALT)


def send_verification_email(user: User) -> None:
    token = make_email_verification_token(user)
    send_templated_email(
        user.email,
        "Verifikasi email Anda",
        "verify_email",
        {"user": user, "link": f"{settings.FRONTEND_URL}/verifikasi-email?token={token}"},
    )


def verify_email_token(token: str) -> User | None:
    try:
        data = signing.loads(token, salt=EMAIL_VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
    except signing.BadSignature:
        return None
    user = User.objects.filter(pk=data.get("uid"), email=data.get("email"), is_active=True).first()
    if user and not user.is_email_verified:
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified", "updated_at"])
    return user


# --- Reset password --------------------------------------------------------------
def send_password_reset_email(email: str) -> None:
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if not user:
        logger.info("Permintaan reset password untuk email tidak terdaftar")
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    send_templated_email(
        user.email,
        "Reset password",
        "password_reset",
        {"user": user, "link": f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"},
    )


def get_user_for_reset(uid: str, token: str) -> User | None:
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        user = User.objects.get(pk=pk, is_active=True)
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        return None
    return user if default_token_generator.check_token(user, token) else None


# --- Status vendor ---------------------------------------------------------------
def set_vendor_status(vendor: VendorProfile, status: str, *, by: User | None = None, reason: str = "") -> None:
    """Ubah status vendor (dipakai Django Admin) dan kirim email hasil keputusan."""
    previous = vendor.status
    vendor.status = status
    if status == VendorStatus.APPROVED:
        vendor.approved_at = timezone.now()
        vendor.approved_by = by
        vendor.rejection_reason = ""
    elif status == VendorStatus.REJECTED:
        vendor.rejection_reason = reason or vendor.rejection_reason
    vendor.save()
    logger.info("Status vendor berubah", extra={"vendor_id": vendor.pk, "from": previous, "to": status})
    if previous != status and status in (VendorStatus.APPROVED, VendorStatus.REJECTED, VendorStatus.SUSPENDED):
        send_templated_email(vendor.user.email, "Status toko Anda", "vendor_status", {"vendor": vendor})
