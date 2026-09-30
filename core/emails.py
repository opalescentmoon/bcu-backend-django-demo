import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_templated_email(to, subject, template, context=None):
    """Kirim email teks dari templates/emails/<template>.txt. Kegagalan hanya dicatat."""
    recipients = [to] if isinstance(to, str) else list(to)
    ctx = {"app_name": settings.APP_NAME, "frontend_url": settings.FRONTEND_URL, **(context or {})}
    body = render_to_string(f"emails/{template}.txt", ctx)
    try:
        send_mail(f"[{settings.APP_NAME}] {subject}", body, None, recipients)
    except Exception:  # noqa: BLE001 — email tidak boleh menggagalkan transaksi
        logger.exception("Gagal mengirim email", extra={"template": template})
