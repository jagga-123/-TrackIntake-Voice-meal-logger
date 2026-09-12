"""Tests for the Whisper wrapper's configuration, using a stand-in model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from meal_voice.exceptions import TranscriptionError
from meal_voice.services.transcriber import Transcriber


@dataclass
class FakeSegment:
    """One decoded chunk, matching the attribute faster-whisper exposes."""

    text: str


@dataclass
class FakeInfo:
    """Language metadata returned alongside the segments."""

    language: str = "hi"
    language_probability: float = 0.97
    duration: float = 4.271


@dataclass
class FakeModel:
    """Records the keyword arguments the transcriber passes to ``transcribe``."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    segments: list[FakeSegment] = field(
        default_factory=lambda: [FakeSegment(" do roti "), FakeSegment(" aur dal ")]
    )

    def transcribe(self, source: Any, **kwargs: Any) -> tuple[list[FakeSegment], FakeInfo]:
        """Stand in for the real decoder."""
        self.calls.append({"source": source, **kwargs})
        return self.segments, FakeInfo()


def transcriber_with(model: Any, **kwargs: Any) -> Transcriber:
    """Build a Transcriber whose cached ``model`` property is already populated."""
    instance = Transcriber(**kwargs)
    instance.__dict__["model"] = model  # bypass the cached_property loader
    return instance


def test_transcribe_joins_segments_and_reports_language() -> None:
    model = FakeModel()

    result = transcriber_with(model).transcribe(b"audio-bytes")

    assert result == {"text": "do roti aur dal", "language": "hi", "duration": 4.27}


def test_decoding_knobs_are_forwarded() -> None:
    model = FakeModel()

    transcriber_with(model, beam_size=1, vad_filter=False).transcribe(b"audio")

    call = model.calls[0]
    assert call["beam_size"] == 1
    assert call["vad_filter"] is False
    assert "meal log" in call["initial_prompt"].lower()


def test_defaults_favour_accuracy() -> None:
    model = FakeModel()

    transcriber_with(model).transcribe(b"audio")

    call = model.calls[0]
    assert call["beam_size"] == 5
    assert call["vad_filter"] is True


def test_bytes_are_wrapped_in_a_stream_and_paths_passed_through(tmp_path: Any) -> None:
    model = FakeModel()
    transcriber_with(model).transcribe(b"raw")
    assert hasattr(model.calls[0]["source"], "read")

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    model2 = FakeModel()
    transcriber_with(model2).transcribe(clip)
    assert model2.calls[0]["source"] == str(clip)


def test_decoder_failure_becomes_a_domain_error() -> None:
    class ExplodingModel:
        def transcribe(self, source: Any, **kwargs: Any) -> None:
            raise RuntimeError("libav could not open the container")

    with pytest.raises(TranscriptionError, match="could not be transcribed"):
        transcriber_with(ExplodingModel()).transcribe(b"not-audio")


def test_empty_segment_list_yields_empty_text() -> None:
    model = FakeModel(segments=[])

    assert transcriber_with(model).transcribe(b"silence")["text"] == ""


def test_pipeline_builds_transcriber_from_settings(settings: Any) -> None:
    from meal_voice.services.pipeline import build_pipeline

    settings.VOICE_MEAL = {
        **settings.VOICE_MEAL,
        "WHISPER_MODEL": "tiny",
        "WHISPER_BEAM_SIZE": 1,
        "WHISPER_CPU_THREADS": 1,
        "WHISPER_VAD_FILTER": False,
        "GEMINI_API_KEY": "test-key",
    }

    # The Gemini client is constructed eagerly, so only the transcriber is asserted.
    transcriber = build_pipeline()._transcriber

    assert transcriber.model_size == "tiny"
    assert transcriber.beam_size == 1
    assert transcriber.cpu_threads == 1
    assert transcriber.vad_filter is False
