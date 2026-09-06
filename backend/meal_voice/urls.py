"""URL routes for meal logging, mounted under ``/api/v1/meal-log/``."""

from __future__ import annotations

from django.urls import path

from meal_voice.views import MealLogListView, VoiceMealConfirmView, VoiceMealPreviewView

app_name = "meal_voice"

urlpatterns = [
    path("", MealLogListView.as_view(), name="meal-log-list"),
    path("voice/preview/", VoiceMealPreviewView.as_view(), name="voice-preview"),
    path("voice/confirm/", VoiceMealConfirmView.as_view(), name="voice-confirm"),
]
