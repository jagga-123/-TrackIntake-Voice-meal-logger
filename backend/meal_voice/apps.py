"""Django app registration for the voice meal ingestion pipeline."""

from __future__ import annotations

from django.apps import AppConfig


class MealVoiceConfig(AppConfig):
    """App config for ``meal_voice``."""

    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "meal_voice"
    verbose_name = "Voice meal ingestion"

    def ready(self) -> None:
        """Start warming the speech-to-text model once the app is loaded."""
        from meal_voice.services.pipeline import start_background_warmup

        start_background_warmup()
