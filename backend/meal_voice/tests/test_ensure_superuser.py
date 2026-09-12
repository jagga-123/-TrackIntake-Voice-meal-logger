"""Tests for the idempotent superuser provisioning command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

pytestmark = pytest.mark.django_db

USER_MODEL = get_user_model()


def run(monkeypatch: pytest.MonkeyPatch, **env: str) -> str:
    """Invoke the command with the given DJANGO_SUPERUSER_* environment."""
    for key in ("USERNAME", "PASSWORD", "EMAIL"):
        monkeypatch.delenv(f"DJANGO_SUPERUSER_{key}", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = StringIO()
    call_command("ensure_superuser", stdout=out)
    return out.getvalue()


def test_creates_the_account_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    output = run(
        monkeypatch,
        DJANGO_SUPERUSER_USERNAME="demo",
        DJANGO_SUPERUSER_PASSWORD="first-password",
        DJANGO_SUPERUSER_EMAIL="demo@example.com",
    )

    user = USER_MODEL.objects.get(username="demo")
    assert user.check_password("first-password")
    assert user.is_superuser and user.is_staff
    assert user.email == "demo@example.com"
    assert "Created" in output


def test_resets_the_password_when_the_account_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    USER_MODEL.objects.create_user(username="demo", password="old-password")

    output = run(
        monkeypatch,
        DJANGO_SUPERUSER_USERNAME="demo",
        DJANGO_SUPERUSER_PASSWORD="rotated-password",
    )

    user = USER_MODEL.objects.get(username="demo")
    assert user.check_password("rotated-password")
    assert not user.check_password("old-password")
    assert user.is_superuser  # an existing plain account is promoted
    assert USER_MODEL.objects.count() == 1
    assert "Updated" in output


def test_running_twice_is_a_no_op_beyond_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"DJANGO_SUPERUSER_USERNAME": "demo", "DJANGO_SUPERUSER_PASSWORD": "same-password"}
    run(monkeypatch, **env)
    run(monkeypatch, **env)

    assert USER_MODEL.objects.filter(username="demo").count() == 1


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"DJANGO_SUPERUSER_USERNAME": "demo"},
        {"DJANGO_SUPERUSER_PASSWORD": "secret"},
        {"DJANGO_SUPERUSER_USERNAME": "   ", "DJANGO_SUPERUSER_PASSWORD": "secret"},
    ],
)
def test_skips_quietly_without_full_credentials(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    output = run(monkeypatch, **env)

    assert USER_MODEL.objects.count() == 0
    assert "leaving accounts untouched" in output
