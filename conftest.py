import shutil

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient

from core.testing import AddressFactory, UserFactory, VendorProfileFactory
from users.models import Role, VendorStatus
from users.services import issue_tokens


@pytest.fixture(autouse=True)
def _reset_state():
    cache.clear()  # throttle
    yield
    for root in (settings.MEDIA_ROOT, settings.PRIVATE_MEDIA_ROOT):
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def api_client():
    return APIClient()


def _auth(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user)['access']}")
    client.user = user
    return client


@pytest.fixture
def client_for(db):
    """client_for(user) → APIClient yang sudah login sebagai user tersebut."""
    return _auth


@pytest.fixture
def customer(db):
    return UserFactory(role=Role.CUSTOMER)


@pytest.fixture
def customer_client(customer):
    return _auth(customer)


@pytest.fixture
def vendor(db):
    return VendorProfileFactory()


@pytest.fixture
def vendor_client(vendor):
    return _auth(vendor.user)


@pytest.fixture
def other_vendor(db):
    return VendorProfileFactory()


@pytest.fixture
def pending_vendor(db):
    return VendorProfileFactory(status=VendorStatus.PENDING)


@pytest.fixture
def admin_user(db):
    return UserFactory(role=Role.ADMIN, is_staff=True, is_superuser=True)


@pytest.fixture
def address(customer):
    return AddressFactory(user=customer)
