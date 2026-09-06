"""Speech-to-text using `faster-whisper <https://github.com/SYSTEMS-AI/faster-whisper>`_.

The Whisper model is loaded lazily on first use and cached on the instance, so
constructing a :class:`Transcriber` is cheap and the multi-second model load
happens once per process.
"""

from __future__ import annotations

import io
import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypedDict

from meal_voice.exceptions import TranscriptionError

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# Nudges Whisper towards romanised Hinglish output and food vocabulary. Whisper
# treats this as preceding context, not as an instruction.
DEFAULT_INITIAL_PROMPT = (
    "Meal log in Hindi, English or Hinglish: do roti, ek katori dal, "
    "half plate rice, ek glass doodh, teen idli sambar ke saath."
)


class TranscriptionResult(TypedDict):
    """Outcome of transcribing one audio clip."""

    text: str
    language: str
    duration: float


class Transcriber:
    """Transcribe short audio clips with automatic language detection.

    Args:
        model_size: Whisper checkpoint name (``tiny``, ``base``, ``small`` ...).
        device: ``cpu`` or ``cuda``.
        compute_type: CTranslate2 quantisation, ``int8`` is the CPU sweet spot.
        beam_size: Decoding beam width; 5 is Whisper's default.
        initial_prompt: Context text that biases decoding towards the meal domain.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        initial_prompt: str = DEFAULT_INITIAL_PROMPT,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt

    @cached_property
    def model(self) -> WhisperModel:
        """Load (and on first use download) the Whisper checkpoint."""
        from faster_whisper import WhisperModel  # heavy import deferred to first use

        logger.info("Loading faster-whisper model '%s' on %s", self.model_size, self.device)
        try:
            return WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:  # model download / CTranslate2 init can fail many ways
            raise TranscriptionError(f"Could not load Whisper model '{self.model_size}'.") from exc

    def transcribe(self, audio: bytes | str | Path) -> TranscriptionResult:
        """Transcribe ``audio`` given as raw bytes or a filesystem path.

        Language is auto-detected so Hindi, English and Hinglish clips all work
        without the caller declaring a language up front.

        Raises:
            TranscriptionError: if the model cannot be loaded or decoding fails.
        """
        source = self._as_source(audio)
        try:
            segments, info = self.model.transcribe(
                source,
                beam_size=self.beam_size,
                vad_filter=True,
                initial_prompt=self.initial_prompt,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except TranscriptionError:
            raise
        except Exception as exc:  # PyAV / CTranslate2 raise a wide range of error types
            raise TranscriptionError("Audio could not be transcribed.") from exc

        logger.info(
            "Transcribed %.1fs of audio (lang=%s, p=%.2f): %r",
            info.duration,
            info.language,
            info.language_probability,
            text,
        )
        return {
            "text": text,
            "language": info.language,
            "duration": round(float(info.duration), 2),
        }

    @staticmethod
    def _as_source(audio: bytes | str | Path) -> str | BinaryIO:
        """Normalise the accepted input types into what faster-whisper consumes."""
        if isinstance(audio, (bytes, bytearray)):
            return io.BytesIO(audio)
        return str(audio)
