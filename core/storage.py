"""Storage publik & privat (docs/02 §6).

STORAGE_BACKEND=local  -> folder lokal; file privat diunduh lewat token bertanda tangan Django.
STORAGE_BACKEND=s3     -> S3/MinIO; file privat diunduh lewat presigned URL.
"""

from functools import cache
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing
from django.core.files.storage import FileSystemStorage
from django.urls import reverse

PRIVATE_DOWNLOAD_SALT = "private-download"


def public_storage():
    return _public_storage()


def private_storage():
    return _private_storage()


@cache
def _public_storage():
    if settings.STORAGE_BACKEND == "s3":
        from storages.backends.s3 import S3Storage

        public = urlparse(settings.AWS_S3_PUBLIC_URL or settings.AWS_S3_ENDPOINT_URL or "")
        kwargs = {"bucket_name": settings.AWS_PUBLIC_BUCKET, "querystring_auth": False, "default_acl": None}
        if public.netloc:
            kwargs["custom_domain"] = f"{public.netloc}/{settings.AWS_PUBLIC_BUCKET}"
            kwargs["url_protocol"] = f"{public.scheme}:"
        return S3Storage(**kwargs)
    return FileSystemStorage(location=settings.MEDIA_ROOT, base_url=settings.MEDIA_URL)


@cache
def _private_storage():
    if settings.STORAGE_BACKEND == "s3":
        from storages.backends.s3 import S3Storage

        return S3Storage(bucket_name=settings.AWS_PRIVATE_BUCKET, default_acl=None, querystring_auth=True)
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)


def reset_storage_cache():
    """Dipakai test ketika setting storage diubah."""
    _public_storage.cache_clear()
    _private_storage.cache_clear()


def private_download_url(name: str, filename: str, ttl_seconds: int, request=None) -> str:
    """URL unduhan sementara untuk file di storage privat."""
    if settings.STORAGE_BACKEND == "s3":
        import boto3
        from botocore.config import Config

        # Ditandatangani dengan host publik (browser), SigV4 + path-style agar kompatibel dengan MinIO.
        client = boto3.client(
            "s3",
            endpoint_url=settings.AWS_S3_PUBLIC_URL or settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        return client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.AWS_PRIVATE_BUCKET,
                "Key": name,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=ttl_seconds,
        )

    token = signing.dumps({"n": name, "f": filename, "t": ttl_seconds}, salt=PRIVATE_DOWNLOAD_SALT)
    path = reverse("private-file", kwargs={"token": token})
    return request.build_absolute_uri(path) if request else path
