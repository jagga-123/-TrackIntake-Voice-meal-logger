"""Create or update the deployment's superuser from environment variables.

``createsuperuser --noinput`` refuses to touch an account that already exists, so
rotating the password on a running deployment silently does nothing. This command
is idempotent: it creates the account when missing and resets the password when
present, which is what a demo environment needs.
"""

from __future__ import annotations

import os
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Ensure a superuser matching the configured credentials exists."""

    help = "Create or update the superuser named by DJANGO_SUPERUSER_* variables."

    def handle(self, *args: Any, **options: Any) -> None:
        """Apply the configured credentials, skipping quietly when unset."""
        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()

        if not username or not password:
            self.stdout.write(
                "DJANGO_SUPERUSER_USERNAME/PASSWORD not set; leaving accounts untouched."
            )
            return

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            **{user_model.USERNAME_FIELD: username},
            defaults={"email": email, "is_staff": True, "is_superuser": True},
        )
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        if email:
            user.email = email
        user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} superuser {username!r}."))
