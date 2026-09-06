"""Request validation and response shaping for the meal-log API."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from meal_voice.models import MacroSource, MealItem, MealLog, MealSource, MealType, Unit
from meal_voice.services.macros import MACRO_KEYS

# Container signatures we accept; browsers label recordings inconsistently, so
# the bytes are trusted over the declared content type.
AUDIO_SIGNATURES: dict[str, Callable[[bytes], bool]] = {
    "webm": lambda h: h.startswith(b"\x1a\x45\xdf\xa3"),
    "wav": lambda h: h.startswith(b"RIFF") and h[8:12] == b"WAVE",
    "mp3": lambda h: h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0),
    "ogg": lambda h: h.startswith(b"OggS"),
    "mp4": lambda h: h[4:8] == b"ftyp",
}


def detect_audio_format(header: bytes) -> str | None:
    """Return the container name matching ``header`` (first 12+ bytes) or ``None``."""
    return next((name for name, matches in AUDIO_SIGNATURES.items() if matches(header)), None)


class MacrosSerializer(serializers.Serializer):
    """Calories and macronutrients; every value must be non-negative."""

    calories = serializers.FloatField(min_value=0)
    protein_g = serializers.FloatField(min_value=0)
    carbs_g = serializers.FloatField(min_value=0)
    fats_g = serializers.FloatField(min_value=0)


class VoiceAudioUploadSerializer(serializers.Serializer):
    """Multipart payload for the preview endpoint."""

    audio = serializers.FileField()

    def validate_audio(self, upload: UploadedFile) -> UploadedFile:
        """Reject empty, oversized or unrecognised audio before it reaches Whisper."""
        max_bytes: int = settings.VOICE_MEAL["MAX_AUDIO_BYTES"]
        if not upload.size:
            raise serializers.ValidationError("The uploaded audio file is empty.", code="invalid_audio")
        if upload.size > max_bytes:
            raise serializers.ValidationError(
                f"Audio exceeds the {max_bytes // (1024 * 1024)} MB limit.", code="audio_too_large"
            )
        header = upload.read(16)
        upload.seek(0)
        if detect_audio_format(header) is None:
            raise serializers.ValidationError(
                "Unsupported or corrupt audio. Upload webm, wav, mp3, ogg or m4a.",
                code="invalid_audio",
            )
        return upload


class ConfirmMealItemSerializer(serializers.Serializer):
    """One (possibly user-edited) preview row submitted for saving."""

    name = serializers.CharField(max_length=120)
    original_text = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=Decimal("0.01"))
    unit = serializers.ChoiceField(choices=Unit.choices)
    assumed_quantity = serializers.BooleanField(required=False, default=False)
    macros = MacrosSerializer()
    macro_source = serializers.ChoiceField(
        choices=MacroSource.choices, required=False, default=MacroSource.LLM_ESTIMATE
    )
    confidence = serializers.FloatField(min_value=0, max_value=1, required=False, default=1.0)


class ConfirmMealSerializer(serializers.Serializer):
    """Edited preview submitted to persist a voice-logged meal.

    ``total_macros`` is intentionally not accepted: totals are recomputed from
    the items on save so the stored log is always internally consistent.
    """

    meal_type = serializers.ChoiceField(choices=MealType.choices)
    logged_at = serializers.DateTimeField(required=False)
    transcript = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    items = ConfirmMealItemSerializer(many=True, allow_empty=False)

    def create(self, validated_data: dict[str, Any]) -> MealLog:
        """Persist the log and its items atomically for the requesting user."""
        items: list[dict[str, Any]] = validated_data["items"]
        with transaction.atomic():
            meal_log = MealLog.objects.create(
                user=self.context["request"].user,
                meal_type=validated_data["meal_type"],
                logged_at=validated_data.get("logged_at") or timezone.now(),
                source=MealSource.VOICE,
                transcript=validated_data.get("transcript") or None,
            )
            MealItem.objects.bulk_create(MealItem(meal_log=meal_log, **item) for item in items)
            meal_log.recalculate_totals()
        return meal_log


class MealItemSerializer(serializers.ModelSerializer):
    """Read-only representation of a stored meal item."""

    class Meta:
        model = MealItem
        fields = (
            "id",
            "name",
            "original_text",
            "quantity",
            "unit",
            "assumed_quantity",
            "macros",
            "macro_source",
            "confidence",
        )
        read_only_fields = fields


class MealLogSerializer(serializers.ModelSerializer):
    """Read-only representation of a stored meal log with nested items."""

    items = MealItemSerializer(many=True, read_only=True)

    class Meta:
        model = MealLog
        fields = (
            "id",
            "meal_type",
            "logged_at",
            "source",
            "transcript",
            "total_macros",
            "items",
            "created_at",
        )
        read_only_fields = fields

    def to_representation(self, instance: MealLog) -> dict[str, Any]:
        """Guarantee every macro key is present even for legacy rows."""
        data = super().to_representation(instance)
        data["total_macros"] = {key: instance.total_macros.get(key, 0.0) for key in MACRO_KEYS}
        return data
