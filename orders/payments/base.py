from dataclasses import dataclass, field
from decimal import Decimal


class PaymentProviderError(Exception):
    """Gagal berkomunikasi dengan payment gateway."""


class InvalidSignature(Exception):
    """Notifikasi tidak lolos verifikasi signature."""


@dataclass
class PaymentInit:
    snap_token: str = ""
    redirect_url: str = ""


@dataclass
class Notification:
    """Notifikasi pembayaran yang sudah diverifikasi & dinormalisasi."""

    provider_order_id: str
    status: str  # nilai PaymentStatus
    gross_amount: Decimal
    payment_type: str = ""
    transaction_id: str = ""
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    name = ""

    def create_payment(self, payment) -> PaymentInit:
        raise NotImplementedError

    def parse_notification(self, data: dict) -> Notification:
        raise NotImplementedError

    def cancel(self, payment) -> None:
        """Best effort; kegagalan tidak boleh menggagalkan pembatalan order."""
