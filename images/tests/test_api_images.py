import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core.storage import PRIVATE_DOWNLOAD_SALT
from core.testing import (
    UserFactory,
    VendorProfileFactory,
    image_category,
    image_upload,
    make_image_bytes,
    make_image_product,
    make_image_purchase,
)
from images.models import DownloadLog, ImageProduct, ImageStatus
from images.services import moderate_image

pytestmark = pytest.mark.django_db

LICENSES = json.dumps(
    [{"license_type": "personal", "price": "50000"}, {"license_type": "commercial", "price": "150000"}]
)


def upload_payload(**overrides):
    return {
        "title": "Rendang Daging Flat Lay",
        "description": "Foto rendang dari atas",
        "category_id": image_category().id,
        "tags": "rendang, #Padang, rendang",
        "file": image_upload("rendang.jpg", 2400, 1600),
        "licenses": LICENSES,
        **overrides,
    }


def _all_strings(data):
    if isinstance(data, dict):
        for value in data.values():
            yield from _all_strings(value)
    elif isinstance(data, list):
        for value in data:
            yield from _all_strings(value)
    else:
        yield str(data)


class TestUpload:
    def test_upload_creates_private_original_and_watermarked_preview(self, vendor_client):
        res = vendor_client.post(reverse("image-list"), upload_payload(), format="multipart")
        assert res.status_code == 201, res.data
        assert res.data["status"] == "pending_review"
        assert res.data["tags"] == ["rendang", "padang"]
        assert res.data["min_price"] == "50000.00"
        assert (res.data["width"], res.data["height"]) == (2400, 1600)

        image = ImageProduct.objects.get(pk=res.data["id"])
        assert Path(settings.PRIVATE_MEDIA_ROOT, image.original_file.name).exists()
        assert not Path(settings.MEDIA_ROOT, image.original_file.name).exists()
        with Image.open(image.watermarked_preview.path) as preview:
            assert max(preview.size) == 1200
            assert not preview.getexif()
            # watermark mengubah piksel dari warna polos aslinya
            assert len(set(preview.getdata())) > 10
        with Image.open(image.thumbnail.path) as thumb:
            assert max(thumb.size) == 400

    def test_response_never_exposes_original(self, vendor_client, api_client):
        res = vendor_client.post(reverse("image-list"), upload_payload(), format="multipart")
        image = ImageProduct.objects.get(pk=res.data["id"])
        moderate_image(image, ImageStatus.PUBLISHED)
        responses = [
            res.data,
            api_client.get(reverse("image-list")).data,
            api_client.get(reverse("image-detail", args=[image.id])).data,
            vendor_client.get(reverse("vendor-images")).data,
        ]
        for data in responses:
            assert not any(image.original_file.name in s or "originals/" in s for s in _all_strings(data))

    @pytest.mark.parametrize(
        "file",
        [
            SimpleUploadedFile("x.jpg", b"bukan gambar", content_type="image/jpeg"),
            SimpleUploadedFile("x.svg", b"<svg></svg>", content_type="image/svg+xml"),
            SimpleUploadedFile("kecil.jpg", make_image_bytes(900, 900), content_type="image/jpeg"),
        ],
        ids=["not-image", "svg", "too-small"],
    )
    def test_rejects_invalid_files(self, vendor_client, file):
        res = vendor_client.post(reverse("image-list"), upload_payload(file=file), format="multipart")
        assert res.status_code == 400
        assert "file" in res.data["error"]["details"]

    def test_rejects_commercial_cheaper_than_personal(self, vendor_client):
        licenses = json.dumps(
            [{"license_type": "personal", "price": "50000"}, {"license_type": "commercial", "price": "10000"}]
        )
        res = vendor_client.post(reverse("image-list"), upload_payload(licenses=licenses), format="multipart")
        assert res.status_code == 400

    def test_duplicate_file_rejected(self, vendor_client):
        raw = make_image_bytes(1600, 1200)
        for expected in (201, 409):
            file = SimpleUploadedFile("a.jpg", raw, content_type="image/jpeg")
            res = vendor_client.post(reverse("image-list"), upload_payload(file=file), format="multipart")
            assert res.status_code == expected
        assert res.data["error"]["code"] == "duplicate_file"

    def test_food_only_vendor_cannot_upload(self, client_for):
        vendor = VendorProfileFactory(sells_images=False)
        res = client_for(vendor.user).post(reverse("image-list"), upload_payload(), format="multipart")
        assert res.status_code == 403

    def test_replacing_file_requires_review_again(self, vendor_client, vendor):
        image = make_image_product(vendor)
        res = vendor_client.patch(
            reverse("image-detail", args=[image.id]),
            {"file": image_upload("baru.png", 1500, 1500, "PNG")},
            format="multipart",
        )
        assert res.status_code == 200, res.data
        assert res.data["status"] == "pending_review"
        assert res.data["orientation"] == "square"

    def test_metadata_edit_keeps_published(self, vendor_client, vendor):
        image = make_image_product(vendor)
        res = vendor_client.patch(reverse("image-detail", args=[image.id]), {"title": "Judul Baru"}, format="multipart")
        assert res.data["status"] == "published"


class TestGallery:
    def test_only_published_images_listed(self, api_client, vendor):
        published = make_image_product(vendor, "Terbit")
        make_image_product(vendor, "Pending", status="pending_review")
        make_image_product(vendor, "Ditolak", status="rejected")
        res = api_client.get(reverse("image-list"))
        assert [r["id"] for r in res.data["results"]] == [published.id]

    def test_filters(self, api_client, vendor):
        personal_only = make_image_product(vendor, "Sate Ayam", licenses=(("personal", "30000"),))
        both = make_image_product(vendor, "Gado Gado")

        def ids(**params):
            return {r["id"] for r in api_client.get(reverse("image-list"), params).data["results"]}

        assert ids(license="commercial") == {both.id}
        assert ids(search="sate") == {personal_only.id}
        assert ids(orientation="landscape") == {personal_only.id, both.id}
        assert ids(orientation="portrait") == set()
        assert ids(max_price=40000) == {personal_only.id}

    def test_detail_shows_purchased_licenses(self, customer_client, vendor):
        image = make_image_product(vendor)
        make_image_purchase(customer_client.user, image, "personal")
        res = customer_client.get(reverse("image-detail", args=[image.id]))
        assert res.data["purchased_licenses"] == ["personal"]
        assert [lic["license_type"] for lic in res.data["licenses"]] == ["personal", "commercial"]

    def test_pending_image_visible_only_to_owner(self, api_client, vendor_client, vendor):
        image = make_image_product(vendor, status="pending_review")
        assert api_client.get(reverse("image-detail", args=[image.id])).status_code == 404
        assert vendor_client.get(reverse("image-detail", args=[image.id])).data["status"] == "pending_review"


class TestDownload:
    def test_not_purchased_forbidden(self, customer_client, vendor):
        image = make_image_product(vendor)
        res = customer_client.get(reverse("image-download", args=[image.id]))
        assert res.status_code == 403
        assert res.data["error"]["code"] == "not_purchased"

    def test_anonymous_unauthorized(self, api_client, vendor):
        image = make_image_product(vendor)
        assert api_client.get(reverse("image-download", args=[image.id])).status_code == 401

    def test_download_link_and_log(self, customer_client, vendor, api_client):
        image = make_image_product(vendor)
        purchase = make_image_purchase(customer_client.user, image, "commercial")
        res = customer_client.get(reverse("image-download", args=[image.id]), HTTP_USER_AGENT="pytest")
        assert res.status_code == 200
        assert res.data["license_type"] == "commercial"
        assert res.data["filename"] == f"{image.slug}.jpg"
        log = DownloadLog.objects.get(purchase=purchase)
        assert log.user_agent == "pytest"
        purchase.refresh_from_db()
        assert purchase.download_count == 1

        # link lokal bertanda tangan mengembalikan file original
        path = res.data["download_url"].replace("http://testserver", "")
        file_res = api_client.get(path)
        assert file_res.status_code == 200
        assert b"".join(file_res.streaming_content) == image.original_file.open("rb").read()
        assert "attachment" in file_res["Content-Disposition"]

    def test_tampered_or_expired_link_rejected(self, api_client, customer, vendor, time_machine):
        image = make_image_product(vendor)
        time_machine.move_to("2026-01-01 10:00:00+07:00")
        token = signing.dumps({"n": image.original_file.name, "f": "x.jpg", "t": 900}, salt=PRIVATE_DOWNLOAD_SALT)
        assert api_client.get(reverse("private-file", args=[token + "x"])).status_code == 404
        time_machine.move_to("2026-01-01 10:15:01+07:00")
        assert api_client.get(reverse("private-file", args=[token])).status_code == 404

    def test_other_user_cannot_use_purchase(self, client_for, vendor):
        image = make_image_product(vendor)
        make_image_purchase(UserFactory(), image)
        assert client_for(UserFactory()).get(reverse("image-download", args=[image.id])).status_code == 403

    def test_soft_deleted_image_still_downloadable(self, customer_client, vendor_client, vendor):
        image = make_image_product(vendor)
        make_image_purchase(customer_client.user, image)
        assert vendor_client.delete(reverse("image-detail", args=[image.id])).status_code == 204
        image.refresh_from_db()
        assert image.is_deleted
        assert customer_client.get(reverse("image-download", args=[image.id])).status_code == 200

    def test_my_downloads(self, customer_client, vendor):
        image = make_image_product(vendor)
        make_image_purchase(customer_client.user, image)
        res = customer_client.get(reverse("my-downloads"))
        assert res.data["count"] == 1
        assert res.data["results"][0]["image"]["title"] == image.title


def test_moderation_sends_email(vendor):
    image = make_image_product(vendor, status="pending_review")
    moderate_image(image, ImageStatus.REJECTED, reason="Buram")
    assert "Buram" in mail.outbox[0].body
