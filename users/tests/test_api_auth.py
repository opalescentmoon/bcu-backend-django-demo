import re

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from core.testing import UserFactory
from users.models import User
from users.services import make_email_verification_token

pytestmark = pytest.mark.django_db
PASSWORD = "RahasiaKuat123"


def register(client, **overrides):
    payload = {
        "username": "budi",
        "email": "Budi@Example.com",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        "role": "customer",
        **overrides,
    }
    return client.post(reverse("auth-register"), payload)


class TestRegister:
    def test_register_creates_user_and_sends_verification(self, api_client):
        res = register(api_client)
        assert res.status_code == 201
        assert res.data["user"]["email"] == "budi@example.com"
        assert res.data["user"]["is_email_verified"] is False
        assert res.data["access"] and res.data["refresh"]
        assert len(mail.outbox) == 1
        assert "/verifikasi-email?token=" in mail.outbox[0].body

    def test_email_is_case_insensitive_unique(self, api_client):
        UserFactory(email="budi@example.com")
        res = register(api_client, email="BUDI@example.com", username="lain")
        assert res.status_code == 400
        assert res.data["error"]["code"] == "validation_error"
        assert "email" in res.data["error"]["details"]

    def test_admin_role_cannot_be_chosen(self, api_client):
        res = register(api_client, role="admin")
        assert res.status_code == 400
        assert "role" in res.data["error"]["details"]

    @pytest.mark.parametrize("password", ["pendek1", "12345678901", "password123"])
    def test_weak_password_rejected(self, api_client, password):
        res = register(api_client, password=password, password_confirm=password)
        assert res.status_code == 400
        assert "password" in res.data["error"]["details"]

    def test_password_confirm_must_match(self, api_client):
        res = register(api_client, password_confirm="BedaSekali123")
        assert res.status_code == 400
        assert "password_confirm" in res.data["error"]["details"]


class TestLogin:
    def test_login_success_returns_tokens_and_user(self, api_client, customer):
        res = api_client.post(reverse("auth-login"), {"email": customer.email.upper(), "password": PASSWORD})
        assert res.status_code == 200
        assert res.data["user"]["id"] == customer.id
        assert {"access", "refresh"} <= set(res.data)

    def test_login_failure_is_generic(self, api_client, customer):
        wrong_pw = api_client.post(reverse("auth-login"), {"email": customer.email, "password": "salah123456"})
        unknown = api_client.post(reverse("auth-login"), {"email": "tidakada@example.com", "password": PASSWORD})
        for res in (wrong_pw, unknown):
            assert res.status_code == 401
            assert res.data["error"]["code"] == "invalid_credentials"
            assert res.data["error"]["message"] == "Email atau password salah."

    def test_login_is_throttled(self, api_client, customer):
        for _ in range(10):
            api_client.post(reverse("auth-login"), {"email": customer.email, "password": "x"})
        res = api_client.post(reverse("auth-login"), {"email": customer.email, "password": PASSWORD})
        assert res.status_code == 429
        assert res.data["error"]["code"] == "throttled"


class TestTokens:
    def test_refresh_rotates_and_blacklists_old(self, api_client, customer):
        login = api_client.post(reverse("auth-login"), {"email": customer.email, "password": PASSWORD})
        old = login.data["refresh"]
        res = api_client.post(reverse("auth-refresh"), {"refresh": old})
        assert res.status_code == 200
        assert res.data["refresh"] != old
        again = api_client.post(reverse("auth-refresh"), {"refresh": old})
        assert again.status_code == 401
        assert again.data["error"]["code"] == "token_not_valid"

    def test_logout_blacklists_refresh(self, client_for, customer):
        refresh = str(RefreshToken.for_user(customer))
        client = client_for(customer)
        assert client.post(reverse("auth-logout"), {"refresh": refresh}).status_code == 205
        assert client.post(reverse("auth-refresh"), {"refresh": refresh}).status_code == 401

    def test_protected_endpoint_requires_token(self, api_client):
        res = api_client.get(reverse("auth-profile"))
        assert res.status_code == 401
        assert res.data["error"]["code"] == "not_authenticated"


class TestEmailVerification:
    def test_verify_email(self, api_client):
        user = UserFactory(is_email_verified=False)
        token = make_email_verification_token(user)
        res = api_client.post(reverse("auth-verify-email"), {"token": token})
        assert res.status_code == 200
        user.refresh_from_db()
        assert user.is_email_verified

    def test_invalid_token(self, api_client):
        res = api_client.post(reverse("auth-verify-email"), {"token": "palsu"})
        assert res.status_code == 400

    def test_expired_token(self, api_client, time_machine):
        user = UserFactory(is_email_verified=False)
        time_machine.move_to("2026-01-01 00:00:00+07:00")
        token = make_email_verification_token(user)
        time_machine.move_to("2026-01-02 00:00:01+07:00")
        assert api_client.post(reverse("auth-verify-email"), {"token": token}).status_code == 400

    def test_resend(self, client_for):
        user = UserFactory(is_email_verified=False)
        res = client_for(user).post(reverse("auth-resend-verification"))
        assert res.status_code == 202
        assert len(mail.outbox) == 1


class TestPasswordReset:
    def test_reset_flow(self, api_client, customer):
        refresh = str(RefreshToken.for_user(customer))
        res = api_client.post(reverse("auth-password-reset"), {"email": customer.email})
        assert res.status_code == 202
        link = re.search(r"uid=(\S+)&token=(\S+)", mail.outbox[0].body)
        res = api_client.post(
            reverse("auth-password-reset-confirm"),
            {"uid": link.group(1), "token": link.group(2), "new_password": "PasswordBaru456"},
        )
        assert res.status_code == 200
        customer.refresh_from_db()
        assert customer.check_password("PasswordBaru456")
        # token lama tidak berlaku, tautan hanya sekali pakai
        assert api_client.post(reverse("auth-refresh"), {"refresh": refresh}).status_code == 401
        again = api_client.post(
            reverse("auth-password-reset-confirm"),
            {"uid": link.group(1), "token": link.group(2), "new_password": "PasswordLain789"},
        )
        assert again.status_code == 400

    def test_unknown_email_does_not_leak(self, api_client):
        res = api_client.post(reverse("auth-password-reset"), {"email": "tidakada@example.com"})
        assert res.status_code == 202
        assert len(mail.outbox) == 0


class TestProfile:
    def test_get_and_patch_profile(self, customer_client):
        res = customer_client.patch(reverse("auth-profile"), {"phone": "0811", "role": "admin", "email": "x@y.z"})
        assert res.status_code == 200
        assert res.data["phone"] == "0811"
        user = User.objects.get(pk=customer_client.user.pk)
        assert user.role == "customer"
        assert user.email != "x@y.z"

    def test_change_password(self, customer_client):
        res = customer_client.post(
            reverse("auth-change-password"), {"old_password": "salah", "new_password": "PasswordBaru456"}
        )
        assert res.status_code == 400
        res = customer_client.post(
            reverse("auth-change-password"), {"old_password": PASSWORD, "new_password": "PasswordBaru456"}
        )
        assert res.status_code == 200
