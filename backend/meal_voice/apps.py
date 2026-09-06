"""Django app registration for the voice meal ingestion pipeline."""

from __future__ import annotations

from django.apps import AppConfig


class MealVoiceConfig(AppConfig):
    """App config for ``meal_voice``."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "meal_voice"
    verbose_name = "Voice meal ingestion"
