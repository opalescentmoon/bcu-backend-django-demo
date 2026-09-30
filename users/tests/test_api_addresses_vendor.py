import pytest
from django.core import mail
from django.urls import reverse

from core.testing import AddressFactory, UserFactory, VendorProfileFactory, image_upload, make_food
from users.models import Role, VendorProfile, VendorStatus
from users.services import set_vendor_status

pytestmark = pytest.mark.django_db

ADDRESS = {
    "label": "Kantor",
    "recipient_name": "Budi",
    "phone": "0812",
    "street": "Jl. Kenanga 1",
    "city": "Bandung",
    "province": "Jawa Barat",
    "postal_code": "40111",
}


class TestAddresses:
    def test_first_address_becomes_default(self, customer_client):
        res = customer_client.post(reverse("address-list"), ADDRESS)
        assert res.status_code == 201
        assert res.data["is_default"] is True

    def test_only_one_default(self, customer_client):
        a = customer_client.post(reverse("address-list"), ADDRESS).data
        b = customer_client.post(reverse("address-list"), {**ADDRESS, "is_default": True}).data
        listing = {x["id"]: x["is_default"] for x in customer_client.get(reverse("address-list")).data}
        assert listing == {a["id"]: False, b["id"]: True}
        customer_client.post(reverse("address-set-default", args=[a["id"]]))
        listing = {x["id"]: x["is_default"] for x in customer_client.get(reverse("address-list")).data}
        assert listing == {a["id"]: True, b["id"]: False}

    def test_deleting_default_promotes_another(self, customer_client):
        a = customer_client.post(reverse("address-list"), ADDRESS).data
        b = customer_client.post(reverse("address-list"), ADDRESS).data
        customer_client.delete(reverse("address-detail", args=[a["id"]]))
        remaining = customer_client.get(reverse("address-list")).data
        assert [(x["id"], x["is_default"]) for x in remaining] == [(b["id"], True)]

    def test_cannot_access_others_address(self, customer_client):
        other = AddressFactory()
        res = customer_client.get(reverse("address-detail", args=[other.id]))
        assert res.status_code == 404


class TestVendorProfile:
    def test_vendor_submits_profile_pending(self, client_for):
        user = UserFactory(role=Role.VENDOR)
        res = client_for(user).post(
            reverse("vendor-profile"),
            {
                "store_name": "Warung Budi",
                "phone": "0812",
                "address": "Jl. A",
                "city": "Bandung",
                "sells_food": True,
                "delivery_fee": "10000",
                "logo": image_upload("logo.png", 300, 300, "PNG"),
                "status": "approved",
            },
            format="multipart",
        )
        assert res.status_code == 201, res.data
        assert res.data["status"] == "pending"
        assert res.data["slug"] == "warung-budi"
        assert res.data["logo_url"].startswith("/media/logos/")

    def test_must_sell_something(self, client_for):
        user = UserFactory(role=Role.VENDOR)
        res = client_for(user).post(
            reverse("vendor-profile"),
            {"store_name": "X", "phone": "1", "address": "a", "city": "b", "sells_food": False, "sells_images": False},
        )
        assert res.status_code == 400

    def test_unverified_vendor_cannot_submit(self, client_for):
        user = UserFactory(role=Role.VENDOR, is_email_verified=False)
        res = client_for(user).post(reverse("vendor-profile"), {"store_name": "X"})
        assert res.status_code == 403
        assert res.data["error"]["code"] == "email_not_verified"

    def test_customer_cannot_access(self, customer_client):
        assert customer_client.get(reverse("vendor-profile")).status_code == 403

    def test_rejected_profile_resubmits_on_patch(self, client_for):
        vendor = VendorProfileFactory(status=VendorStatus.REJECTED, rejection_reason="Foto kurang jelas")
        res = client_for(vendor.user).patch(reverse("vendor-profile"), {"description": "Sudah diperbaiki"})
        assert res.status_code == 200
        assert res.data["status"] == "pending"

    def test_set_status_sends_email(self, admin_user):
        vendor = VendorProfileFactory(status=VendorStatus.PENDING)
        set_vendor_status(vendor, VendorStatus.APPROVED, by=admin_user)
        vendor.refresh_from_db()
        assert vendor.approved_by == admin_user and vendor.approved_at
        assert "Disetujui" in mail.outbox[0].body

    def test_public_vendor_page(self, api_client, vendor):
        make_food(vendor)
        res = api_client.get(reverse("vendor-public", args=[vendor.slug]))
        assert res.status_code == 200
        assert res.data["food_count"] == 1

    def test_public_vendor_hidden_when_not_approved(self, api_client, pending_vendor):
        assert api_client.get(reverse("vendor-public", args=[pending_vendor.slug])).status_code == 404

    def test_effective_commission(self, vendor):
        assert str(vendor.effective_commission_rate) == "0.1000"
        vendor.commission_rate = "0.0500"
        vendor.save()
        assert str(VendorProfile.objects.get(pk=vendor.pk).effective_commission_rate) == "0.0500"
