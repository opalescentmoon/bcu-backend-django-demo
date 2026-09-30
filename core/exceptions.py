"""Format error standar API (docs/04 §1)."""

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class BusinessError(exceptions.APIException):
    """Pelanggaran aturan bisnis. Default HTTP 409."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"
    default_detail = "Permintaan bertentangan dengan aturan bisnis."

    def __init__(self, code, message, details=None, status_code=None):
        super().__init__(detail=message, code=code)
        self.code = code
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code


def _error(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _first_code(exc):
    codes = exc.get_codes()
    if isinstance(codes, str):
        return codes
    if isinstance(codes, dict) and isinstance(codes.get("code"), str):
        return codes["code"]
    return exc.default_code


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
        exc = exceptions.ValidationError(detail=detail)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API error", exc_info=exc)
        if settings.DEBUG:
            return None
        return Response(
            _error("server_error", "Terjadi kesalahan pada server."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, BusinessError):
        response.data = _error(exc.code, exc.message, exc.details)
    elif isinstance(exc, exceptions.ValidationError):
        details = response.data
        if isinstance(details, list):
            details = {"non_field_errors": details}
        response.data = _error("validation_error", "Data yang dikirim tidak valid.", details)
    else:
        detail = exc.detail
        if isinstance(detail, dict):  # mis. simplejwt InvalidToken
            message = str(detail.get("detail", ""))
        else:
            message = str(detail)
        response.data = _error(_first_code(exc), message)
    return response
