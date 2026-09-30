from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from django.urls import reverse
from moto import mock_aws

from core import storage


@pytest.fixture
def s3_settings(settings):
    settings.STORAGE_BACKEND = "s3"
    settings.AWS_S3_ENDPOINT_URL = None
    settings.AWS_S3_PUBLIC_URL = "http://localhost:9000"
    settings.AWS_ACCESS_KEY_ID = "test"
    settings.AWS_SECRET_ACCESS_KEY = "test"
    settings.AWS_STORAGE_BUCKET_NAME = None
    storage.reset_storage_cache()
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=settings.AWS_PUBLIC_BUCKET)
        s3.create_bucket(Bucket=settings.AWS_PRIVATE_BUCKET)
        yield settings
    storage.reset_storage_cache()


def test_s3_private_presigned_url(s3_settings):
    url = storage.private_download_url("originals/abc.jpg", "rendang.jpg", 900)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "localhost:9000"
    assert parsed.path == f"/{s3_settings.AWS_PRIVATE_BUCKET}/originals/abc.jpg"
    assert query["X-Amz-Expires"] == ["900"]
    assert "attachment" in query["response-content-disposition"][0]


def test_s3_public_url_uses_public_host_without_signature(s3_settings):
    url = storage.public_storage().url("previews/x.jpg")
    assert url == f"http://localhost:9000/{s3_settings.AWS_PUBLIC_BUCKET}/previews/x.jpg"


def test_s3_private_storage_is_signed(s3_settings):
    from django.core.files.base import ContentFile

    private = storage.private_storage()
    name = private.save("originals/secret.jpg", ContentFile(b"data"))
    assert "X-Amz-Signature" in private.url(name)


def test_private_file_view_disabled_for_s3(s3_settings, client):
    assert client.get(reverse("private-file", args=["apa-saja"])).status_code == 404


@pytest.mark.django_db
def test_health(api_client):
    res = api_client.get(reverse("health"))
    assert res.status_code == 200
    assert res.data == {"status": "ok", "checks": {"database": "ok", "storage": "ok"}}
    assert res["X-Request-ID"]


@pytest.mark.django_db
def test_request_id_is_propagated(api_client):
    res = api_client.get(reverse("health"), HTTP_X_REQUEST_ID="abc12345-demo")
    assert res["X-Request-ID"] == "abc12345-demo"


@pytest.mark.django_db
def test_unknown_error_returns_standard_json(api_client, settings, monkeypatch):
    from food import views

    def boom(*args, **kwargs):
        raise RuntimeError("tak terduga")

    monkeypatch.setattr(views.FoodViewSet, "list", boom)
    res = api_client.get(reverse("food-list"))
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "server_error"
