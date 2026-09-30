"""Integrasi Midtrans Snap (sandbox). Lihat docs/05 §4."""

import hashlib
import hmac
import logging
import math
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

from .base import InvalidSignature, Notification, PaymentInit, PaymentProvider, PaymentProviderError

logger = logging.getLogger(__name__)
TIMEOUT = 15


def _urls():
    if settings.MIDTRANS_IS_PRODUCTION:
        return "https://app.midtrans.com", "https://api.midtrans.com"
    return "https://app.sandbox.midtrans.com", "https://api.sandbox.midtrans.com"


def signature_for(order_id: str, status_code: str, gross_amount: str) -> str:
    raw = f"{order_id}{status_code}{gross_amount}{settings.MIDTRANS_SERVER_KEY}"
    return hashlib.sha512(raw.encode()).hexdigest()


def map_status(transaction_status: str, fraud_status: str | None) -> str | None:
    if transaction_status == "capture":
        return "settlement" if fraud_status in (None, "accept") else "pending"
    if transaction_status in ("settlement", "pending", "deny", "cancel", "expire", "failure"):
        return transaction_status
    return None  # refund/chargeback dll. ditangani manual


class MidtransProvider(PaymentProvider):
    name = "midtrans"

    def _auth(self):
        return (settings.MIDTRANS_SERVER_KEY, "")

    def create_payment(self, payment) -> PaymentInit:
        order = payment.order
        items = [
            {
                "id": str(item.pk),
                "price": int(item.unit_price),
                "quantity": item.quantity,
                "name": f"{item.title_snapshot} {item.variant_snapshot or item.license_type}".strip()[:50],
            }
            for item in order.items.all()
        ]
        if order.delivery_fee_total:
            items.append(
                {"id": "ongkir", "price": int(order.delivery_fee_total), "quantity": 1, "name": "Ongkos kirim"}
            )
        remaining = max(1, math.ceil((order.expires_at - timezone.now()).total_seconds() / 60))
        body = {
            "transaction_details": {"order_id": payment.provider_order_id, "gross_amount": int(payment.amount)},
            "item_details": items,
            "customer_details": {"first_name": order.user.username, "email": order.user.email},
            "expiry": {"unit": "minute", "duration": remaining},
        }
        app_url, _ = _urls()
        try:
            res = requests.post(f"{app_url}/snap/v1/transactions", json=body, auth=self._auth(), timeout=TIMEOUT)
            res.raise_for_status()
            data = res.json()
        except (requests.RequestException, ValueError) as exc:
            logger.exception("Gagal membuat transaksi Midtrans")
            raise PaymentProviderError("Gagal menghubungi Midtrans.") from exc
        return PaymentInit(snap_token=data.get("token", ""), redirect_url=data.get("redirect_url", ""))

    def fetch_status(self, provider_order_id: str) -> dict:
        _, api_url = _urls()
        try:
            res = requests.get(f"{api_url}/v2/{provider_order_id}/status", auth=self._auth(), timeout=TIMEOUT)
            res.raise_for_status()
            return res.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaymentProviderError("Gagal mengecek status ke Midtrans.") from exc

    def parse_notification(self, data: dict) -> Notification:
        expected = signature_for(
            str(data.get("order_id", "")), str(data.get("status_code", "")), str(data.get("gross_amount", ""))
        )
        if not hmac.compare_digest(expected, str(data.get("signature_key", ""))):
            raise InvalidSignature
        # Jangan percaya isi notifikasi begitu saja: konfirmasi ulang ke API status (docs/07 §2).
        confirmed = self.fetch_status(data["order_id"])
        status = map_status(confirmed.get("transaction_status", ""), confirmed.get("fraud_status"))
        return Notification(
            provider_order_id=data["order_id"],
            status=status,
            gross_amount=Decimal(str(confirmed.get("gross_amount", data.get("gross_amount")))),
            payment_type=confirmed.get("payment_type", ""),
            transaction_id=confirmed.get("transaction_id", ""),
            raw=data,
        )

    def cancel(self, payment) -> None:
        _, api_url = _urls()
        try:
            requests.post(f"{api_url}/v2/{payment.provider_order_id}/cancel", auth=self._auth(), timeout=TIMEOUT)
        except requests.RequestException:
            logger.warning("Gagal membatalkan transaksi Midtrans %s", payment.provider_order_id)
