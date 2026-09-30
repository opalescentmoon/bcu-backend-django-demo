from .base import Notification, PaymentInit, PaymentProvider

RESULT_TO_STATUS = {"success": "settlement", "failure": "deny", "expire": "expire"}


class SimulationProvider(PaymentProvider):
    """Provider palsu untuk development & demo offline (docs/05 §4.3)."""

    name = "simulation"

    def create_payment(self, payment) -> PaymentInit:
        return PaymentInit()

    def parse_notification(self, data: dict) -> Notification:
        return Notification(
            provider_order_id=data["provider_order_id"],
            status=RESULT_TO_STATUS[data["result"]],
            gross_amount=data["gross_amount"],
            payment_type="simulation",
            transaction_id=f"SIM-{data['provider_order_id']}",
            raw=data | {"gross_amount": str(data["gross_amount"])},
        )
