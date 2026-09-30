from rest_framework.permissions import BasePermission


class IsEmailVerified(BasePermission):
    code = "email_not_verified"
    message = "Verifikasi email Anda terlebih dahulu."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_email_verified)


class IsVendor(BasePermission):
    code = "permission_denied"
    message = "Hanya untuk akun vendor."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == "vendor")


class IsApprovedVendor(BasePermission):
    """Role vendor + profil toko berstatus approved (dibaca dari DB, bukan klaim JWT)."""

    code = "vendor_not_approved"
    message = "Toko Anda belum disetujui admin."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.role == "vendor"):
            return False
        vendor = getattr(user, "vendor_profile", None)
        return bool(vendor and vendor.status == "approved")


class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == "admin" and user.is_staff)
