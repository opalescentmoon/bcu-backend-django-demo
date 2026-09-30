from django.conf import settings

from .base import InvalidSignature, Notification, PaymentInit, PaymentProvider, PaymentProviderError
from .midtrans import MidtransProvider
from .simulation import SimulationProvider

PROVIDERS = {"midtrans": MidtransProvider, "simulation": SimulationProvider}


def get_provider(name: str | None = None) -> PaymentProvider:
    return PROVIDERS[name or settings.PAYMENT_PROVIDER]()


__all__ = [
    "InvalidSignature",
    "Notification",
    "PaymentInit",
    "PaymentProvider",
    "PaymentProviderError",
    "get_provider",
]
