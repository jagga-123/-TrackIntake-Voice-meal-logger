"""Tests for process-wide pipeline caching and background model warm-up."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from meal_voice.services import pipeline as pipeline_module
from meal_voice.services.pipeline import (
    VoiceMealPipeline,
    get_pipeline,
    serves_http,
    start_background_warmup,
)


class RecordingTranscriber:
    """Transcriber double that counts how often the model is loaded."""

    def __init__(self) -> None:
        self.loads = 0

    @property
    def model(self) -> str:
        """Stand in for the lazily loaded Whisper checkpoint."""
        self.loads += 1
        return "loaded-model"


@pytest.fixture(autouse=True)
def reset_pipeline_singleton() -> Any:
    """Clear the module-level pipeline so tests cannot leak into each other."""
    pipeline_module._pipeline = None
    yield
    pipeline_module._pipeline = None


def make_pipeline() -> tuple[VoiceMealPipeline, RecordingTranscriber]:
    """Build a pipeline whose only real behaviour is model loading."""
    transcriber = RecordingTranscriber()
    return VoiceMealPipeline(transcriber, parser=object(), nutrition=object()), transcriber


# --- serves_http --------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["/usr/local/bin/gunicorn", "config.wsgi:application"], True),
        (["gunicorn"], True),
        (["manage.py", "runserver"], True),
        (["manage.py", "migrate"], False),
        (["manage.py", "ensure_superuser"], False),
        (["pytest"], False),
        ([], False),
    ],
)
def test_serves_http_recognises_web_server_processes(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: bool
) -> None:
    monkeypatch.setattr("sys.argv", argv)
    assert serves_http() is expected


# --- get_pipeline -------------------------------------------------------------


def test_get_pipeline_builds_once_and_reuses_the_instance() -> None:
    built, _ = make_pipeline()

    with patch.object(pipeline_module, "build_pipeline", return_value=built) as build:
        first, second = get_pipeline(), get_pipeline()

    assert first is second is built
    assert build.call_count == 1


def test_warm_up_loads_the_model_once_and_reports_elapsed_time() -> None:
    pipeline, transcriber = make_pipeline()

    elapsed = pipeline.warm_up()

    assert transcriber.loads == 1
    assert elapsed >= 0


# --- start_background_warmup --------------------------------------------------


def test_warmup_is_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
    settings.VOICE_MEAL = {**settings.VOICE_MEAL, "WARM_MODELS_ON_STARTUP": False}
    monkeypatch.setattr("sys.argv", ["gunicorn"])

    assert start_background_warmup() is None


def test_warmup_is_skipped_for_one_off_commands(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.VOICE_MEAL = {**settings.VOICE_MEAL, "WARM_MODELS_ON_STARTUP": True}
    monkeypatch.setattr("sys.argv", ["manage.py", "migrate"])

    assert start_background_warmup() is None


def test_warmup_loads_the_shared_pipeline_in_a_thread(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.VOICE_MEAL = {**settings.VOICE_MEAL, "WARM_MODELS_ON_STARTUP": True}
    monkeypatch.setattr("sys.argv", ["gunicorn"])
    built, transcriber = make_pipeline()

    with patch.object(pipeline_module, "build_pipeline", return_value=built):
        thread = start_background_warmup()
        assert thread is not None
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert transcriber.loads == 1
        # A request arriving afterwards reuses the warmed instance.
        assert get_pipeline() is built
        assert transcriber.loads == 1


def test_warmup_failure_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """A broken model must not take the worker down at startup."""
    settings.VOICE_MEAL = {**settings.VOICE_MEAL, "WARM_MODELS_ON_STARTUP": True}
    monkeypatch.setattr("sys.argv", ["gunicorn"])

    with patch.object(pipeline_module, "build_pipeline", side_effect=RuntimeError("no model")):
        thread = start_background_warmup()
        assert thread is not None
        thread.join(timeout=5)

    assert not thread.is_alive()
