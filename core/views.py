from django.conf import settings
from django.core import signing
from django.db import connection
from django.http import FileResponse, Http404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .storage import PRIVATE_DOWNLOAD_SALT, private_storage


@extend_schema(tags=["utilitas"], responses={200: dict})
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])
def health(request):
    checks = {}
    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    try:
        private_storage().exists("healthcheck")
        checks["storage"] = "ok"
    except Exception:  # noqa: BLE001
        checks["storage"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return Response({"status": "ok" if healthy else "error", "checks": checks}, status=200 if healthy else 503)


def private_file(request, token):
    """Unduhan file privat untuk STORAGE_BACKEND=local (pengganti presigned URL S3)."""
    if settings.STORAGE_BACKEND != "local":
        raise Http404
    try:
        unsigned = signing.loads(token, salt=PRIVATE_DOWNLOAD_SALT, max_age=None)
        signing.loads(token, salt=PRIVATE_DOWNLOAD_SALT, max_age=unsigned["t"])
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise Http404 from exc
    storage = private_storage()
    if not storage.exists(unsigned["n"]):
        raise Http404
    return FileResponse(storage.open(unsigned["n"], "rb"), as_attachment=True, filename=unsigned["f"])
