"""HTTP-level tests for the preview, confirm and list endpoints."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AbstractBaseUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from meal_voice.exceptions import LLMUnavailableError, MealParseError, TranscriptionError
from meal_voice.models import MealItem, MealLog
from meal_voice.tests.conftest import wav_bytes

pytestmark = pytest.mark.django_db

PREVIEW_URL = reverse("meal_voice:voice-preview")
CONFIRM_URL = reverse("meal_voice:voice-confirm")
LIST_URL = reverse("meal_voice:meal-log-list")

SAMPLE_PREVIEW: dict[str, Any] = {
    "transcript": "do roti aur ek katori dal",
    "language": "hi",
    "duration": 2.4,
    "meal_type": "lunch",
    "meal_type_source": "transcript",
    "source": "voice",
    "confidence": 0.92,
    "items": [
        {
            "name": "roti", "original_text": "do roti", "quantity": 2.0, "unit": "piece",
            "assumed_quantity": False, "macro_source": "table", "matched_food": "Roti",
            "confidence": 0.92,
            "macros": {"calories": 208, "protein_g": 6.2, "carbs_g": 36, "fats_g": 5.2},
        },
        {
            "name": "dal", "original_text": "ek katori dal", "quantity": 1.0, "unit": "bowl",
            "assumed_quantity": False, "macro_source": "table", "matched_food": "Dal",
            "confidence": 0.92,
            "macros": {"calories": 150, "protein_g": 9, "carbs_g": 20, "fats_g": 3.5},
        },
    ],
    "total_macros": {"calories": 358, "protein_g": 15.2, "carbs_g": 56, "fats_g": 8.7},
}


class StubPipeline:
    """Stands in for the real pipeline so no Whisper or LLM is touched."""

    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[bytes, AbstractBaseUser]] = []

    def run(self, audio_bytes: bytes, user: AbstractBaseUser) -> dict[str, Any]:
        """Record the call and either raise the configured error or return the result."""
        self.calls.append((audio_bytes, user))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def upload(content: bytes, name: str = "clip.wav", content_type: str = "audio/wav") -> SimpleUploadedFile:
    """Wrap raw bytes as a multipart file field."""
    return SimpleUploadedFile(name, content, content_type=content_type)


def confirm_payload(**overrides: Any) -> dict[str, Any]:
    """Build the JSON body the frontend sends after the user reviews the preview.

    Rows are deep-copied so tests that mutate the payload cannot leak into
    ``SAMPLE_PREVIEW`` and change the behaviour of later tests.
    """
    payload = {
        "meal_type": "lunch",
        "transcript": SAMPLE_PREVIEW["transcript"],
        "items": [
            {key: copy.deepcopy(value) for key, value in row.items() if key != "matched_food"}
            for row in SAMPLE_PREVIEW["items"]
        ],
    }
    payload.update(overrides)
    return payload


# --- POST /voice/preview/ ---------------------------------------------------------


def test_preview_requires_authentication(api_client: APIClient) -> None:
    response = api_client.post(PREVIEW_URL, {"audio": upload(wav_bytes())}, format="multipart")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_preview_returns_pipeline_output(auth_client: APIClient, user: AbstractBaseUser) -> None:
    stub = StubPipeline(result=SAMPLE_PREVIEW)
    audio = wav_bytes()

    with patch("meal_voice.views.get_pipeline", return_value=stub):
        response = auth_client.post(PREVIEW_URL, {"audio": upload(audio)}, format="multipart")

    assert response.status_code == 200
    assert response.json() == SAMPLE_PREVIEW
    assert stub.calls == [(audio, user)]
    assert MealLog.objects.count() == 0  # preview never persists


@pytest.mark.parametrize(
    ("content", "name", "content_type"),
    [
        (b"hello this is not audio", "notes.txt", "text/plain"),
        (b"", "empty.wav", "audio/wav"),
        (b"<html></html>", "page.webm", "audio/webm"),
    ],
)
def test_preview_rejects_invalid_audio(
    auth_client: APIClient, content: bytes, name: str, content_type: str
) -> None:
    stub = StubPipeline(result=SAMPLE_PREVIEW)

    with patch("meal_voice.views.get_pipeline", return_value=stub):
        response = auth_client.post(
            PREVIEW_URL, {"audio": upload(content, name, content_type)}, format="multipart"
        )

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert "audio" in body["details"]
    assert stub.calls == []


def test_preview_rejects_missing_file(auth_client: APIClient) -> None:
    response = auth_client.post(PREVIEW_URL, {}, format="multipart")

    assert response.status_code == 400
    assert "audio" in response.json()["error"]["details"]


def test_preview_rejects_oversized_audio(auth_client: APIClient, settings: Any) -> None:
    settings.VOICE_MEAL = {**settings.VOICE_MEAL, "MAX_AUDIO_BYTES": 100}

    response = auth_client.post(PREVIEW_URL, {"audio": upload(wav_bytes(200))}, format="multipart")

    assert response.status_code == 400
    assert "limit" in response.json()["error"]["details"]["audio"][0]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (MealParseError("No food items were detected in the transcript."), 422, "meal_parse_failed"),
        (TranscriptionError(), 502, "transcription_failed"),
        (LLMUnavailableError("Gemini request failed (ClientError 429)."), 502, "llm_unavailable"),
    ],
)
def test_preview_maps_pipeline_errors_to_http(
    auth_client: APIClient, error: Exception, status: int, code: str
) -> None:
    with patch("meal_voice.views.get_pipeline", return_value=StubPipeline(error=error)):
        response = auth_client.post(PREVIEW_URL, {"audio": upload(wav_bytes())}, format="multipart")

    assert response.status_code == status
    assert response.json()["error"] == {"code": code, "message": str(error)}


# --- POST /voice/confirm/ ---------------------------------------------------------


def test_confirm_persists_log_and_items(auth_client: APIClient, user: AbstractBaseUser) -> None:
    response = auth_client.post(CONFIRM_URL, confirm_payload(), format="json")

    assert response.status_code == 201
    body = response.json()
    log = MealLog.objects.get(pk=body["id"])
    assert log.user == user
    assert log.source == "voice"
    assert log.meal_type == "lunch"
    assert log.transcript == SAMPLE_PREVIEW["transcript"]
    assert log.items.count() == 2

    assert body["source"] == "voice"
    assert [row["name"] for row in body["items"]] == ["roti", "dal"]
    assert body["items"][0]["quantity"] == 2.0
    assert body["items"][0]["macro_source"] == "table"
    assert body["total_macros"] == {"calories": 358.0, "protein_g": 15.2, "carbs_g": 56.0, "fats_g": 8.7}


def test_confirm_recomputes_totals_from_edited_items(auth_client: APIClient) -> None:
    payload = confirm_payload()
    payload["items"] = [payload["items"][0]]  # user deleted the dal row
    payload["items"][0]["quantity"] = 3
    payload["items"][0]["macros"] = {"calories": 312, "protein_g": 9.3, "carbs_g": 54, "fats_g": 7.8}
    payload["total_macros"] = {"calories": 1, "protein_g": 1, "carbs_g": 1, "fats_g": 1}  # ignored

    response = auth_client.post(CONFIRM_URL, payload, format="json")

    assert response.status_code == 201
    assert response.json()["total_macros"]["calories"] == 312.0
    assert MealItem.objects.count() == 1


def test_confirm_accepts_explicit_logged_at(auth_client: APIClient) -> None:
    response = auth_client.post(
        CONFIRM_URL, confirm_payload(logged_at="2026-09-05T08:30:00+05:30"), format="json"
    )

    assert response.status_code == 201
    assert response.json()["logged_at"].startswith("2026-09-05T08:30:00")


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (lambda p: p.update(items=[]), "items"),
        (lambda p: p.update(meal_type="brunch"), "meal_type"),
        (lambda p: p["items"][0].update(unit="katori"), "items"),
        (lambda p: p["items"][0].update(quantity=0), "items"),
        (lambda p: p["items"][0]["macros"].update(calories=-5), "items"),
        (lambda p: p.pop("meal_type"), "meal_type"),
    ],
)
def test_confirm_rejects_invalid_payloads(auth_client: APIClient, mutation: Any, field: str) -> None:
    payload = confirm_payload()
    mutation(payload)

    response = auth_client.post(CONFIRM_URL, payload, format="json")

    assert response.status_code == 400
    assert field in response.json()["error"]["details"]
    assert MealLog.objects.count() == 0


def test_confirm_requires_authentication(api_client: APIClient) -> None:
    response = api_client.post(CONFIRM_URL, confirm_payload(), format="json")

    assert response.status_code == 401


# --- GET /meal-log/ ----------------------------------------------------------------


def test_list_is_paginated_and_scoped_to_user(
    auth_client: APIClient, user: AbstractBaseUser, other_user: AbstractBaseUser
) -> None:
    for _ in range(3):
        auth_client.post(CONFIRM_URL, confirm_payload(), format="json")
    MealLog.objects.create(user=other_user, meal_type="dinner")

    response = auth_client.get(LIST_URL, {"page_size": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert len(body["results"]) == 2
    assert body["next"] is not None
    assert all(len(row["items"]) == 2 for row in body["results"])


def test_list_newest_first(auth_client: APIClient) -> None:
    auth_client.post(CONFIRM_URL, confirm_payload(logged_at="2026-09-01T09:00:00+05:30"), format="json")
    auth_client.post(CONFIRM_URL, confirm_payload(logged_at="2026-09-03T09:00:00+05:30"), format="json")

    response = auth_client.get(LIST_URL)

    logged = [row["logged_at"][:10] for row in response.json()["results"]]
    assert logged == ["2026-09-03", "2026-09-01"]


def test_jwt_bearer_token_grants_access(api_client: APIClient, user: AbstractBaseUser) -> None:
    token_response = api_client.post(
        reverse("token_obtain_pair"), {"username": "asha", "password": "secret-pass-123"}, format="json"
    )
    assert token_response.status_code == 200
    access = token_response.json()["access"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    response = api_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.json()["count"] == 0
