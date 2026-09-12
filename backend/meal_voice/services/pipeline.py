"""End-to-end orchestration: audio → transcript → parsed items → macros → preview.

The pipeline never writes to the database. It returns a preview the user can
correct in the UI; persistence happens only through the confirm endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.utils import timezone
from pydantic import BaseModel, ConfigDict

from meal_voice.exceptions import MealParseError
from meal_voice.models import MealSource, MealType, Unit
from meal_voice.services.macros import Macros
from meal_voice.services.meal_parser import MealParser, ParsedItem, build_llm_client
from meal_voice.services.nutrition_lookup import MacroSourceLiteral, NutritionLookup
from meal_voice.services.transcriber import Transcriber

logger = logging.getLogger(__name__)

# Penalty applied to an item's confidence when its quantity was assumed rather than spoken.
ASSUMED_QUANTITY_PENALTY = 0.15


class PreviewItem(BaseModel):
    """One editable row of the preview shown to the user."""

    model_config = ConfigDict(frozen=True)

    name: str
    original_text: str
    quantity: float
    unit: Unit
    assumed_quantity: bool
    macros: Macros
    macro_source: MacroSourceLiteral
    matched_food: str | None
    confidence: float


class MealPreview(BaseModel):
    """Unsaved result of the voice pipeline, mirroring the ``MealLog`` shape."""

    model_config = ConfigDict(frozen=True)

    transcript: str
    language: str
    duration: float
    meal_type: MealType
    meal_type_source: Literal["transcript", "time_of_day"]
    source: MealSource
    confidence: float
    items: list[PreviewItem]
    total_macros: Macros


def infer_meal_type_from_time(now: datetime | None = None) -> MealType:
    """Guess the eating occasion from the local hour when the transcript gives no clue."""
    hour = (now or timezone.localtime()).hour
    if 5 <= hour < 11:
        return MealType.BREAKFAST
    if 11 <= hour < 16:
        return MealType.LUNCH
    if 19 <= hour < 24:
        return MealType.DINNER
    return MealType.SNACK


class VoiceMealPipeline:
    """Chain transcription, LLM parsing and nutrition lookup into a preview."""

    def __init__(
        self, transcriber: Transcriber, parser: MealParser, nutrition: NutritionLookup
    ) -> None:
        self._transcriber = transcriber
        self._parser = parser
        self._nutrition = nutrition

    def run(self, audio_bytes: bytes, user: AbstractBaseUser) -> dict[str, Any]:
        """Produce a JSON-serialisable meal preview for ``user`` from ``audio_bytes``.

        Raises:
            TranscriptionError: speech-to-text failed (HTTP 502).
            LLMUnavailableError: the LLM provider failed (HTTP 502).
            MealParseError: no meal could be extracted (HTTP 422).
        """
        transcription = self._transcriber.transcribe(audio_bytes)
        if not transcription["text"]:
            raise MealParseError("The recording contained no recognisable speech.")

        parsed = self._parser.parse(transcription["text"], language=transcription["language"])
        items = [self._enrich(item, parsed.confidence) for item in parsed.items]
        total = Macros.total(item.macros for item in items).rounded()

        if parsed.meal_type is None:
            meal_type, meal_type_source = infer_meal_type_from_time(), "time_of_day"
        else:
            meal_type, meal_type_source = parsed.meal_type, "transcript"

        preview = MealPreview(
            transcript=transcription["text"],
            language=transcription["language"],
            duration=transcription["duration"],
            meal_type=meal_type,
            meal_type_source=meal_type_source,
            source=MealSource.VOICE,
            confidence=parsed.confidence,
            items=items,
            total_macros=total,
        )
        logger.info(
            "Preview for user=%s: %d items, %.0f kcal, meal_type=%s (%s)",
            user.pk, len(items), total.calories, meal_type, meal_type_source,
        )
        return preview.model_dump(mode="json")

    def _enrich(self, item: ParsedItem, meal_confidence: float) -> PreviewItem:
        """Attach table-backed macros (when available) and a per-item confidence."""
        assert item.quantity is not None  # guaranteed by ParsedItem.apply_defaults
        nutrition = self._nutrition.resolve(item.name, item.quantity, item.unit, item.macros)
        confidence = meal_confidence
        if item.assumed_quantity:
            confidence = max(0.0, confidence - ASSUMED_QUANTITY_PENALTY)
        return PreviewItem(
            name=item.name,
            original_text=item.original_text,
            quantity=item.quantity,
            unit=item.unit,
            assumed_quantity=item.assumed_quantity,
            macros=nutrition.macros,
            macro_source=nutrition.macro_source,
            matched_food=nutrition.matched_food,
            confidence=round(confidence, 2),
        )


def build_pipeline() -> VoiceMealPipeline:
    """Assemble a pipeline from Django settings.

    Raises:
        LLMUnavailableError: when no LLM provider is configured.
    """
    config = settings.VOICE_MEAL
    transcriber = Transcriber(
        model_size=config["WHISPER_MODEL"],
        device=config["WHISPER_DEVICE"],
        compute_type=config["WHISPER_COMPUTE_TYPE"],
        beam_size=config["WHISPER_BEAM_SIZE"],
        cpu_threads=config["WHISPER_CPU_THREADS"],
        vad_filter=config["WHISPER_VAD_FILTER"],
    )
    return VoiceMealPipeline(
        transcriber=transcriber,
        parser=MealParser(build_llm_client()),
        nutrition=NutritionLookup(),
    )


@lru_cache(maxsize=1)
def get_pipeline() -> VoiceMealPipeline:
    """Process-wide pipeline so the Whisper model is loaded once, not per request."""
    return build_pipeline()
