import pytest
import responses
from django.urls import reverse

from orders.models import Order
from orders.payments.midtrans import signature_for

from .conftest import add_food, checkout

pytestmark = pytest.mark.django_db
SNAP_URL = "https://app.sandbox.midtrans.com/snap/v1/transactions"
API = "https://api.sandbox.midtrans.com/v2"


@pytest.fixture
def midtrans(settings):
    settings.PAYMENT_PROVIDER = "midtrans"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.post(
            SNAP_URL, json={"token": "snap-token-123", "redirect_url": "https://app.sandbox.midtrans.com/snap/v4/x"}
        )
        yield rsps


@pytest.fixture
def pending_order(midtrans, customer_client, address, two_vendor_foods):
    add_food(customer_client, two_vendor_foods[0], 2)
    res = checkout(customer_client, "food", address)
    assert res.data["payment"]["snap_token"] == "snap-token-123"
    assert res.data["payment"]["client_key"] == "SB-Mid-client-TEST"
    return Order.objects.get(order_number=res.data["order"]["order_number"])


def notification(order, status="settlement", gross="60000.00", status_code="200", signature=None):
    order_id = order.payments.get().provider_order_id
    return {
        "order_id": order_id,
        "status_code": status_code,
        "gross_amount": gross,
        "transaction_status": status,
        "signature_key": signature or signature_for(order_id, status_code, gross),
    }


def test_snap_request_body(midtrans, pending_order):
    import json

    body = json.loads(midtrans.calls[0].request.body)
    assert body["transaction_details"] == {"order_id": f"{pending_order.order_number}-1", "gross_amount": 60000}
    assert sum(i["price"] * i["quantity"] for i in body["item_details"]) == 60000


def test_valid_notification_confirmed_via_status_api(midtrans, pending_order, api_client):
    payload = notification(pending_order)
    midtrans.get(
        f"{API}/{payload['order_id']}/status",
        json={
            "transaction_status": "settlement",
            "gross_amount": "60000.00",
            "payment_type": "bank_transfer",
            "transaction_id": "tx-1",
        },
    )
    res = api_client.post(reverse("midtrans-notification"), payload, format="json")
    assert res.status_code == 200
    pending_order.refresh_from_db()
    assert pending_order.status == "paid"
    assert pending_order.payment_method == "bank_transfer"


def test_invalid_signature_rejected(midtrans, pending_order, api_client):
    res = api_client.post(
        reverse("midtrans-notification"), notification(pending_order, signature="palsu"), format="json"
    )
    assert res.status_code == 403
    pending_order.refresh_from_db()
    assert pending_order.status == "pending"


def test_status_api_wins_over_notification(midtrans, pending_order, api_client):
    payload = notification(pending_order, status="settlement")
    midtrans.get(
        f"{API}/{payload['order_id']}/status", json={"transaction_status": "pending", "gross_amount": "60000.00"}
    )
    api_client.post(reverse("midtrans-notification"), payload, format="json")
    pending_order.refresh_from_db()
    assert pending_order.status == "pending"


def test_capture_challenge_is_pending(midtrans, pending_order, api_client):
    payload = notification(pending_order, status="capture")
    midtrans.get(
        f"{API}/{payload['order_id']}/status",
        json={"transaction_status": "capture", "fraud_status": "challenge", "gross_amount": "60000.00"},
    )
    api_client.post(reverse("midtrans-notification"), payload, format="json")
    pending_order.refresh_from_db()
    assert pending_order.status == "pending"


def test_snap_failure_still_creates_order(settings, customer_client, address, two_vendor_foods):
    settings.PAYMENT_PROVIDER = "midtrans"
    with responses.RequestsMock() as rsps:
        rsps.post(SNAP_URL, status=500)
        add_food(customer_client, two_vendor_foods[0])
        res = checkout(customer_client, "food", address)
    assert res.status_code == 201
    assert res.data["payment"] is None
    assert res.data["order"]["can_pay"] is True
