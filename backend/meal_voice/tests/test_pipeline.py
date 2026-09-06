"""Tests for ``VoiceMealPipeline`` with a fake transcriber and scripted LLM."""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import AbstractBaseUser

from meal_voice.exceptions import LLMUnavailableError, MealParseError, TranscriptionError
from meal_voice.models import MealType
from meal_voice.services.meal_parser import MealParser
from meal_voice.services.nutrition_lookup import NutritionLookup
from meal_voice.services.pipeline import VoiceMealPipeline, infer_meal_type_from_time
from meal_voice.tests.conftest import FakeTranscriber, ScriptedLLMClient, item, macros, meal_json

pytestmark = pytest.mark.django_db


def build_pipeline(transcript: str, *replies: str) -> tuple[VoiceMealPipeline, FakeTranscriber]:
    """Wire a pipeline whose only real component is the nutrition table."""
    transcriber = FakeTranscriber(transcript)
    parser = MealParser(ScriptedLLMClient(*replies))
    return VoiceMealPipeline(transcriber, parser, NutritionLookup()), transcriber


def test_run_returns_unsaved_preview_with_table_and_llm_macros(user: AbstractBaseUser) -> None:
    reply = meal_json(
        [
            item("roti", 2, "piece", macros=macros(120, 4, 20, 3)),
            item("quinoa salad", None, "bowl", macros=macros(210, 8, 30, 6)),
        ],
        meal_type="lunch",
        confidence=0.9,
    )
    pipeline, transcriber = build_pipeline("do roti aur quinoa salad", reply)

    preview = pipeline.run(b"fake-audio", user)

    assert transcriber.received == [b"fake-audio"]
    assert preview["transcript"] == "do roti aur quinoa salad"
    assert preview["language"] == "hi"
    assert preview["source"] == "voice"
    assert preview["meal_type"] == "lunch"
    assert preview["meal_type_source"] == "transcript"

    roti, salad = preview["items"]
    assert roti["macro_source"] == "table"
    assert roti["matched_food"] == "Roti"
    assert roti["macros"]["calories"] == pytest.approx(208)  # table value, not the LLM's 120
    assert roti["confidence"] == 0.9

    assert salad["macro_source"] == "llm_estimate"
    assert salad["matched_food"] is None
    assert salad["macros"]["calories"] == 210
    assert salad["assumed_quantity"] is True
    assert salad["confidence"] == pytest.approx(0.75)  # 0.9 minus the assumed-quantity penalty

    assert preview["total_macros"]["calories"] == pytest.approx(418)
    assert preview["total_macros"]["protein_g"] == pytest.approx(6.2 + 8)


def test_meal_type_falls_back_to_time_of_day(user: AbstractBaseUser) -> None:
    pipeline, _ = build_pipeline("do roti", meal_json([item("roti", 2, "piece")], meal_type=None))

    preview = pipeline.run(b"audio", user)

    assert preview["meal_type"] in set(MealType.values)
    assert preview["meal_type_source"] == "time_of_day"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(6, MealType.BREAKFAST), (10, MealType.BREAKFAST), (13, MealType.LUNCH), (17, MealType.SNACK),
     (20, MealType.DINNER), (23, MealType.DINNER), (2, MealType.SNACK)],
)
def test_infer_meal_type_from_time(hour: int, expected: MealType) -> None:
    assert infer_meal_type_from_time(datetime(2026, 9, 6, hour, 30)) == expected


def test_silent_recording_raises_parse_error(user: AbstractBaseUser) -> None:
    pipeline, _ = build_pipeline("")

    with pytest.raises(MealParseError, match="no recognisable speech"):
        pipeline.run(b"audio", user)


def test_transcriber_failure_propagates(user: AbstractBaseUser) -> None:
    class BrokenTranscriber:
        def transcribe(self, audio: bytes) -> dict[str, object]:
            raise TranscriptionError("model download failed")

    pipeline = VoiceMealPipeline(BrokenTranscriber(), MealParser(ScriptedLLMClient()), NutritionLookup())

    with pytest.raises(TranscriptionError):
        pipeline.run(b"audio", user)


def test_llm_failure_propagates(user: AbstractBaseUser) -> None:
    class DownLLM:
        provider = "down"

        def complete(self, system: str, messages: list[dict[str, str]]) -> str:
            raise LLMUnavailableError("rate limited")

    pipeline = VoiceMealPipeline(FakeTranscriber("do roti"), MealParser(DownLLM()), NutritionLookup())

    with pytest.raises(LLMUnavailableError):
        pipeline.run(b"audio", user)
