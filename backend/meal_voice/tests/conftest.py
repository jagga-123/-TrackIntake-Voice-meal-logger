"""Shared fixtures and test doubles for the meal_voice test suite."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from rest_framework.test import APIClient

from meal_voice.services.meal_parser import ChatMessage
from meal_voice.services.transcriber import TranscriptionResult


class ScriptedLLMClient:
    """LLM double that replays canned replies and records every request."""

    provider = "scripted"

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    def complete(self, system: str, messages: list[ChatMessage]) -> str:
        """Return the next scripted reply, remembering the conversation sent."""
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("ScriptedLLMClient ran out of replies")
        return self._replies.pop(0)


class FakeTranscriber:
    """Transcriber double returning a fixed result without touching Whisper."""

    def __init__(self, text: str, language: str = "hi", duration: float = 3.2) -> None:
        self.result: TranscriptionResult = {
            "text": text,
            "language": language,
            "duration": duration,
        }
        self.received: list[bytes] = []

    def transcribe(self, audio: bytes) -> TranscriptionResult:
        """Record the bytes received and return the canned transcription."""
        self.received.append(audio)
        return self.result


def macros(calories: float, protein_g: float, carbs_g: float, fats_g: float) -> dict[str, float]:
    """Build a macro dict in the shape the LLM emits."""
    return {"calories": calories, "protein_g": protein_g, "carbs_g": carbs_g, "fats_g": fats_g}


def item(name: str, quantity: Any, unit: str, **overrides: Any) -> dict[str, Any]:
    """Build one LLM item dict with sensible defaults, overridable per test."""
    payload: dict[str, Any] = {
        "name": name,
        "original_text": name,
        "quantity": quantity,
        "unit": unit,
        "assumed_quantity": False,
        "macros": macros(100, 5, 10, 3),
    }
    payload.update(overrides)
    return payload


def meal_json(items: list[dict[str, Any]], meal_type: str | None = None, confidence: float = 0.9) -> str:
    """Serialise a full LLM reply."""
    return json.dumps({"meal_type": meal_type, "items": items, "confidence": confidence})


def wav_bytes(payload_size: int = 64) -> bytes:
    """Return a minimal RIFF/WAVE container so upload validation passes."""
    return b"RIFF" + (36 + payload_size).to_bytes(4, "little") + b"WAVE" + b"\0" * payload_size


@pytest.fixture
def user(db: None) -> AbstractBaseUser:
    """A persisted user for authenticated requests."""
    return get_user_model().objects.create_user(username="asha", password="secret-pass-123")


@pytest.fixture
def other_user(db: None) -> AbstractBaseUser:
    """A second user, used to prove listings are scoped per user."""
    return get_user_model().objects.create_user(username="ravi", password="secret-pass-456")


@pytest.fixture
def api_client() -> APIClient:
    """Unauthenticated DRF test client."""
    return APIClient()


@pytest.fixture
def auth_client(api_client: APIClient, user: AbstractBaseUser) -> APIClient:
    """DRF test client already authenticated as ``user``."""
    api_client.force_authenticate(user)
    return api_client
